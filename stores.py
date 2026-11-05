"""Storage engines. One interface, four ways to hold the same catalogue:

  postgresql://user:pass@host/db          a PostgreSQL restore (pg_restore / psql), JSONB payloads
  sqlite:///path/to/anilist.sqlite        the release's .sqlite file (or one made by load_dump.py)
  duckdb:///path/to/anilist.duckdb        the release's .duckdb file
  parquet:///path/to/folder-or-file       the release's Parquet files, queried in place through DuckDB

Every engine opens the data read-only. The routes never see SQL: they hand a filter dict to a Store and get
plain records back, so the JSON is identical whichever engine is behind it.

Derived from code by JsmaAtom (MIT). See LICENSE.
"""
import json, os, threading, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

TABLE = "anilist_catalog"
REQUIRED = ("id", "payload", "search_text", "status", "season_year", "season", "format", "popularity", "trending", "genres", "tags",
            "next_airing_at", "indexed_at", "is_adult", "favourites", "average_score", "media_updated_at", "start_key")
SORT_COLUMNS = {"POPULARITY": "popularity", "SCORE": "average_score", "TRENDING": "trending", "START_DATE": "start_key", "FAVOURITES": "favourites", "UPDATED_AT": "media_updated_at"}
STATEMENT_TIMEOUT_MS = int(os.getenv("ANISERVE_STATEMENT_TIMEOUT_MS", "30000"))


class StoreUnavailable(Exception):
    """The data cannot be reached right now (connection lost, file missing, schema not there). Maps to HTTP 503."""


def iso(v):
    """Timestamps come back as datetime (PostgreSQL, DuckDB) or text (SQLite); give clients one format."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):  # DuckDB timestamps arrive as epoch seconds (see ts())
        return datetime.fromtimestamp(v, timezone.utc).isoformat()
    if isinstance(v, datetime):
        return (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat()
    return v


def like_pattern(word):
    return "%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def order_sql(key, direction):
    """key: id | next_airing_at | a name from SORT_COLUMNS. Missing values sort last and ties fall back to popularity, then id, so pages never overlap."""
    if key == "id":
        return f"id {direction}"
    if key == "next_airing_at":
        return "next_airing_at ASC, id"
    col = SORT_COLUMNS[key]
    tie = "" if key == "POPULARITY" else "popularity DESC NULLS LAST, "
    return f"{col} {direction} NULLS LAST, {tie}id ASC"


def not_found(kind, path):
    """The error for a missing file, with the fix for the most common mistake: sqlite:///var/x (relative) where sqlite:////var/x (absolute) was meant."""
    msg = f"{kind} not found: {path}"
    if not os.path.isabs(path) and os.path.exists("/" + path):
        msg += f". That is a relative path; for the absolute path /{path} use four slashes after the scheme (sqlite:////{path})"
    elif not os.path.isabs(path):
        msg += f" (relative to {os.getcwd()})"
    return msg


def as_record(v):
    return json.loads(v) if isinstance(v, (str, bytes)) else v


class Store:
    engine = "?"
    ph = "?"
    kind_label = "?"

    # ---- dialect hooks -------------------------------------------------------------------------
    def like_clause(self):            raise NotImplementedError
    def like_param(self, word):       return like_pattern(word)
    def contains_all(self, col, vals): raise NotImplementedError   # -> (sql, params)
    def colour_expr(self):            raise NotImplementedError   # SQL for payload.coverImage.color
    def ts(self, expr):               return expr                 # SQL that returns a timestamp in a form iso() understands
    def not_adult(self):              raise NotImplementedError
    def airing_clause(self):          raise NotImplementedError   # -> (sql, params)
    def genre_rows_sql(self, where):  raise NotImplementedError
    def query(self, *stmts):          raise NotImplementedError   # run (sql, params) pairs in one transaction -> [fetchall...]
    def check(self):                  raise NotImplementedError   # -> None or a problem description
    def close(self):                  pass
    def table_ref(self, name=TABLE):  return name

    # ---- shared logic --------------------------------------------------------------------------
    def where(self, f, adult):
        clauses, params = [], []
        for word in f.get("words") or ():
            clauses.append(self.like_clause()); params.append(self.like_param(word))
        for col in ("season_year", "season", "format", "status"):
            key = {"season_year": "year"}.get(col, col)
            if f.get(key) is not None:
                clauses.append(f"{col} = {self.ph}"); params.append(f[key])
        for col in ("genres", "tags"):
            if f.get(col):
                sql, p = self.contains_all(col, f[col]); clauses.append(sql); params += p
        if f.get("ids"):
            clauses.append(f"id IN ({', '.join([self.ph] * len(f['ids']))})"); params += list(f["ids"])
        if f.get("airing"):
            sql, p = self.airing_clause(); clauses.append(sql); params += p
        if not adult:
            clauses.append(self.not_adult())
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", params

    def page(self, f, order, page, n, adult, ids_only=False):
        """-> (total, rows). rows are catalogue records (dicts), or plain ids when ids_only."""
        where, params = self.where(f, adult); t = self.table_ref()
        total, rows = self.query(
            (f"SELECT count(*) FROM {t}{where}", params),
            (f"SELECT {'id' if ids_only else 'payload'} FROM {t}{where} ORDER BY {order_sql(*order)} LIMIT {self.ph} OFFSET {self.ph}", [*params, n, (page - 1) * n]))
        return total[0][0], [r[0] if ids_only else as_record(r[0]) for r in rows]

    def after(self, last_id, size, adult):
        """Keyset batch for exports: records with id greater than last_id, in id order."""
        where = f" WHERE id > {self.ph}" + ("" if adult else f" AND {self.not_adult()}")
        (rows,) = self.query((f"SELECT payload FROM {self.table_ref()}{where} ORDER BY id LIMIT {self.ph}", [last_id, size]))
        return [as_record(r[0]) for r in rows]

    def adult_ids(self, ids):
        ids = list(ids)
        if not ids: return set()
        marks = ", ".join([self.ph] * len(ids))
        (rows,) = self.query((f"SELECT id FROM {self.table_ref()} WHERE id IN ({marks}) AND NOT {self.not_adult()}", ids))
        return {r[0] for r in rows}

    def colours(self, ids):
        ids = list(ids)
        if not ids: return {}
        marks = ", ".join([self.ph] * len(ids))
        (rows,) = self.query((f"SELECT id, {self.colour_expr()} FROM {self.table_ref()} WHERE id IN ({marks})", ids))
        return {i: c for i, c in rows}

    def blocks(self, anilist_id, kind, first, last):
        try:
            (rows,) = self.query((f"SELECT page, items FROM {self.table_ref('anilist_detail_pages')} WHERE id = {self.ph} AND kind = {self.ph} AND page BETWEEN {self.ph} AND {self.ph} ORDER BY page", [anilist_id, kind, first, last]))
        except StoreUnavailable:
            raise
        except Exception:  # the table is optional: a catalogue without stored detail pages still works
            return {}
        return {p: as_record(items) for p, items in rows}

    def stats(self, adult):
        t = self.table_ref(); where = "" if adult else f" WHERE {self.not_adult()}"
        and_ = (" AND " if where else " WHERE ")
        asql, ap = self.airing_clause()
        head, decades, genres, status, fmt = self.query(
            (f"SELECT count(*), {self.ts('max(indexed_at)')}, sum(CASE WHEN {asql} THEN 1 ELSE 0 END), min(season_year), max(season_year) FROM {t}{where}", ap),
            (f"SELECT season_year - season_year % 10 AS k, count(*) FROM {t}{where}{and_}season_year IS NOT NULL GROUP BY 1 ORDER BY 1", []),
            (self.genre_rows_sql(where), []),
            (f"SELECT status, count(*) FROM {t}{where} GROUP BY 1 ORDER BY 2 DESC, 1", []),
            (f"SELECT format, count(*) FROM {t}{where} GROUP BY 1 ORDER BY 2 DESC, 1", []))
        total, last, airing, y0, y1 = head[0]
        rows = lambda r: [{"key": k, "count": int(n)} for k, n in r]
        try:
            (jobs,) = self.query((f"SELECT key, {self.ts('updated_at')} FROM {self.table_ref('anilist_index_state')} ORDER BY key", []))
        except StoreUnavailable:
            raise
        except Exception as exc:  # the table is optional, but say so if it exists and fails
            jobs = []
            if "does not exist" not in str(exc) and "no such table" not in str(exc).lower() and "not exist" not in str(exc).lower():
                import logging; logging.getLogger("aniserve").warning("could not read anilist_index_state: %s", exc)
        return {"entries": int(total), "airing": int(airing or 0), "firstYear": y0, "lastYear": y1, "lastIndexedAt": iso(last),
                "status": rows(status), "format": rows(fmt), "decades": rows(decades), "genres": rows(genres), "refreshJobs": {k: iso(u) for k, u in jobs}}


# --------------------------------------------------------------------------------------------------
class PostgresStore(Store):
    engine = "postgresql"
    ph = "%s"

    def __init__(self, url, schema=None, size=10):
        import psycopg2, psycopg2.pool
        self.pg = psycopg2; self.schema = schema; self.size = size
        self.slots = threading.BoundedSemaphore(size); self.used = {}
        # No startup options: connection poolers (PgBouncer, Supabase, Neon) reject them.
        # Read-only is requested per transaction instead (psycopg2 issues BEGIN READ ONLY).
        try:
            self.pool = psycopg2.pool.ThreadedConnectionPool(1, size, url, keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=5, connect_timeout=15)
        except psycopg2.Error as exc:
            raise StoreUnavailable(str(exc).strip()) from exc

    def table_ref(self, name=TABLE):
        return f'"{self.schema}"."{name}"' if self.schema else name

    def colour_expr(self):            return "payload #>> '{coverImage,color}'"
    def like_clause(self):            return "search_text ILIKE %s"
    def contains_all(self, col, vals): return f"{col} @> %s", [list(vals)]
    def not_adult(self):              return "is_adult = false"
    def airing_clause(self):          return "next_airing_at > now()", []
    def genre_rows_sql(self, where):  return f"SELECT g, count(*) FROM (SELECT unnest(genres) AS g FROM {self.table_ref()}{where}) s GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 12"

    def _checkout(self):
        """A live connection. One idle for a while is pinged first, so one the server dropped is replaced instead of failing a request."""
        for _ in range(3):
            conn = self.pool.getconn()
            if conn.closed or time.monotonic() - self.used.get(id(conn), 0) > 20:
                try:
                    with conn.cursor() as cur: cur.execute("SELECT 1")
                    conn.rollback()
                except Exception:
                    self.pool.putconn(conn, close=True); continue
            self.used[id(conn)] = time.monotonic()
            conn.readonly = True
            return conn
        raise StoreUnavailable("database unreachable")

    def query(self, *stmts):
        if not self.slots.acquire(timeout=30):
            raise StoreUnavailable("timed out waiting for a database connection")
        conn = None
        try:
            conn = self._checkout()
            out = []
            with conn.cursor() as cur:
                cur.execute(f"SET LOCAL statement_timeout = {int(STATEMENT_TIMEOUT_MS)}")
                for sql, params in stmts:
                    cur.execute(sql, params or None); out.append(cur.fetchall())
            return out
        except (self.pg.OperationalError, self.pg.InterfaceError) as exc:
            if conn is not None: conn.close()  # putconn below discards it
            raise StoreUnavailable("database connection lost") from exc
        finally:
            if conn is not None:
                try: conn.rollback()  # read-only: nothing to commit, just end the transaction
                except Exception: pass
                self.pool.putconn(conn, close=bool(conn.closed))
            self.slots.release()

    def check(self):
        try:
            (found,) = self.query(("SELECT to_regclass(%s) IS NOT NULL", [self.table_ref()]))
            if not found[0][0]: return f"table {TABLE} not found; restore a dump into this database first"
            (got,) = self.query(("SELECT array_agg(column_name::text) FROM information_schema.columns WHERE table_name = %s" + (" AND table_schema = %s" if self.schema else ""), [TABLE] + ([self.schema] if self.schema else [])))
            cols = got[0][0]
        except StoreUnavailable as exc:
            return str(exc)
        missing = [c for c in REQUIRED if c not in (cols or [])]
        return f"{TABLE} is missing columns: {', '.join(missing)}" if missing else None

    def close(self):
        try: self.pool.closeall()
        except Exception: pass


# --------------------------------------------------------------------------------------------------
class SQLiteStore(Store):
    engine = "sqlite"

    def __init__(self, path):
        import sqlite3
        self.sqlite3 = sqlite3; self.path = str(Path(path).expanduser()); self.local = threading.local()
        if not Path(self.path).is_file():
            raise StoreUnavailable(not_found("SQLite file", self.path))
        self.conn()  # fail now, not on the first request

    def conn(self):
        c = getattr(self.local, "c", None)
        if c is None:
            try:
                c = self.sqlite3.connect(Path(self.path).resolve().as_uri() + "?mode=ro", uri=True, check_same_thread=False)
            except self.sqlite3.Error as exc:
                raise StoreUnavailable(f"cannot open {self.path}: {exc}") from exc
            c.execute("PRAGMA query_only = ON"); c.execute("PRAGMA cache_size = -65536")
            c.create_function("ucase", 1, lambda s: s.casefold() if isinstance(s, str) else s, deterministic=True)  # Unicode-aware, unlike SQLite's built-in LIKE
            self.local.c = c
        return c

    def colour_expr(self):            return "json_extract(payload, '$.coverImage.color')"
    def like_clause(self):            return "ucase(search_text) LIKE ? ESCAPE '\\'"
    def like_param(self, word):       return like_pattern(word.casefold())
    def contains_all(self, col, vals):
        return " AND ".join([f"EXISTS (SELECT 1 FROM json_each({col}) WHERE value = ?)"] * len(vals)), list(vals)
    def not_adult(self):              return "COALESCE(is_adult, 0) = 0"
    def airing_clause(self):          return "next_airing_at > ?", [datetime.now(timezone.utc).isoformat()]  # ISO text in UTC sorts like time
    def genre_rows_sql(self, where):  return f"SELECT j.value, count(*) FROM {TABLE}, json_each({TABLE}.genres) j{where} GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 12"

    def query(self, *stmts):
        try:
            c = self.conn()
            return [c.execute(sql, params).fetchall() for sql, params in stmts]
        except self.sqlite3.OperationalError as exc:
            raise StoreUnavailable(str(exc)) from exc

    def check(self):
        try:
            (cols,) = self.query((f"SELECT name FROM pragma_table_info('{TABLE}')", []))
        except StoreUnavailable as exc:
            return str(exc)
        have = {r[0] for r in cols}
        if not have: return f"table {TABLE} not found in {self.path}"
        missing = [c for c in REQUIRED if c not in have]
        return f"{TABLE} is missing columns: {', '.join(missing)}" if missing else None

    def close(self):
        c = getattr(self.local, "c", None)
        if c: c.close()


# --------------------------------------------------------------------------------------------------
class DuckDBStore(Store):
    """A .duckdb file, or a folder of Parquet files exposed as views (data stays on disk) or loaded into memory."""
    engine = "duckdb"

    def __init__(self, path, parquet=False, memory=True):
        import duckdb
        self.duckdb = duckdb; self.path = str(Path(path).expanduser()); self.parquet = parquet
        if parquet:
            self.engine = "parquet"
            self.con = duckdb.connect(":memory:")
            self._mount_parquet(memory)
        elif memory:
            if not Path(self.path).is_file():
                raise StoreUnavailable(not_found("DuckDB file", self.path))
            self.con = duckdb.connect(":memory:")
            self._copy_from_file()
        else:
            if not Path(self.path).is_file():
                raise StoreUnavailable(not_found("DuckDB file", self.path))
            try:
                self.con = duckdb.connect(self.path, read_only=True)
            except duckdb.Error as exc:
                raise StoreUnavailable(f"cannot open {self.path}: {exc}") from exc
        self.schema = self._find_schema()

    def _copy_from_file(self):
        """Load the .duckdb file into memory: queries run several times faster than on the compressed file, and the file is released at once."""
        try:
            self.con.execute("ATTACH ? AS src (READ_ONLY)", [self.path])
            tables = self.con.execute("SELECT table_schema, table_name FROM information_schema.tables WHERE table_catalog = 'src' AND table_type = 'BASE TABLE'").fetchall()
            for schema, name in tables:
                if schema != "main": self.con.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
                self.con.execute(f'CREATE TABLE "{schema}"."{name}" AS SELECT * FROM src."{schema}"."{name}"')
            self.con.execute("DETACH src")
        except self.duckdb.Error as exc:
            raise StoreUnavailable(f"cannot open {self.path}: {exc}") from exc

    def _mount_parquet(self, load):
        p = Path(self.path)
        folder = p if p.is_dir() else p.parent
        files = {}
        for f in folder.glob("*.parquet"):
            name = f.stem.split(".")[-1]  # public.anilist_catalog.parquet -> anilist_catalog
            files[name] = f
        if p.is_file(): files[p.stem.split(".")[-1]] = p
        if TABLE not in files:
            raise StoreUnavailable(not_found("Parquet folder", str(folder)) if not folder.is_dir() else f"no {TABLE} Parquet file in {folder}; unzip the release's -parquet.zip there")
        for name, f in files.items():
            quoted = str(f).replace("'", "''")
            self.con.execute(f'CREATE {"TABLE" if load else "VIEW"} "{name}" AS SELECT * FROM read_parquet(\'{quoted}\')')

    def _find_schema(self):
        try:
            rows = self.con.execute("SELECT table_schema FROM information_schema.tables WHERE table_name = ?", [TABLE]).fetchall()
        except self.duckdb.Error:
            return None
        schemas = [r[0] for r in rows]
        return ("public" if "public" in schemas else schemas[0]) if schemas else None  # the release file keeps the Postgres schema name

    def table_ref(self, name=TABLE):
        return f'"{self.schema}"."{name}"' if self.schema and self.schema != "main" else name  # the release file keeps the Postgres schema name

    def colour_expr(self):            return "json_extract_string(payload, '$.coverImage.color')"
    def ts(self, expr):               return f"epoch({expr})"
    def like_clause(self):            return "search_text ILIKE ? ESCAPE '\\'"
    def contains_all(self, col, vals): return " AND ".join([f"list_contains({col}, ?)"] * len(vals)), list(vals)
    def not_adult(self):              return "COALESCE(is_adult, false) = false"
    def airing_clause(self):          return "next_airing_at > now()", []
    def genre_rows_sql(self, where):  return f"SELECT g, count(*) FROM (SELECT unnest(genres) AS g FROM {self.table_ref()}{where}) s GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 12"

    def query(self, *stmts):
        try:
            cur = self.con.cursor()  # one cursor per call: safe across threads
            try:
                return [cur.execute(sql, params).fetchall() for sql, params in stmts]
            finally:
                cur.close()
        except self.duckdb.ConnectionException as exc:
            raise StoreUnavailable(str(exc)) from exc

    def check(self):
        if not self.schema: return f"table {TABLE} not found in {self.path}"
        try:
            (cols,) = self.query(("SELECT column_name FROM information_schema.columns WHERE table_name = ?", [TABLE]))
        except StoreUnavailable as exc:
            return str(exc)
        have = {r[0] for r in cols}
        missing = [c for c in REQUIRED if c not in have]
        return f"{TABLE} is missing columns: {', '.join(missing)}" if missing else None

    def close(self):
        try: self.con.close()
        except Exception: pass


# --------------------------------------------------------------------------------------------------
def describe(url):
    """The connection string with any password hidden, for logs and /health."""
    if "://" not in url: return url
    parts = urlsplit(url)
    if parts.password:
        host = parts.hostname + (f":{parts.port}" if parts.port else "")
        return urlunsplit((parts.scheme, f"{parts.username}:***@{host}", parts.path, "", ""))
    return url


def detect(url):
    """-> (engine, target). Accepts URLs (postgresql://, sqlite:///, duckdb:///, parquet:///) or a bare file path chosen by its extension."""
    u = url.strip()
    low = u.lower()
    if low.startswith(("postgres://", "postgresql://")): return "postgresql", u
    for scheme in ("sqlite", "duckdb", "parquet"):
        if low.startswith(scheme + ":"):
            rest = u.split("://", 1)[1] if "://" in u else u.split(":", 1)[1]
            return scheme, rest[1:] if rest.startswith("/") else rest  # sqlite:///rel -> rel, sqlite:////abs -> /abs
    p = Path(u).expanduser()
    if p.suffix.lower() in {".sqlite", ".sqlite3", ".db"}: return "sqlite", str(p)
    if p.suffix.lower() == ".duckdb": return "duckdb", str(p)
    if p.suffix.lower() == ".parquet" or p.is_dir(): return "parquet", str(p)
    raise StoreUnavailable("DATABASE_URL must start with postgresql://, sqlite:///, duckdb:/// or parquet:///, or be a path ending in .sqlite, .duckdb or .parquet")


def open_store(url):
    engine, target = detect(url)
    if engine == "postgresql":
        return PostgresStore(target, schema=os.getenv("ANISERVE_SCHEMA") or None, size=max(2, int(os.getenv("DATABASE_POOL_SIZE", "10"))))
    if engine == "sqlite": return SQLiteStore(target)
    memory = os.getenv("ANISERVE_DUCKDB_MODE", "memory").strip().lower() != "disk"  # DuckDB and Parquet: load into RAM (fast, default) or read the files in place
    if engine == "duckdb": return DuckDBStore(target, memory=memory)
    return DuckDBStore(target, parquet=True, memory=memory)


class Catalog:
    """Holds the open store and reconnects by itself when the data was unreachable at startup or the connection drops."""
    def __init__(self, url):
        self.url = url; self.store = None; self.problem = None if url else "DATABASE_URL is not set"; self._tried = 0.0

    @property
    def ok(self): return self.store is not None and self.problem is None

    @property
    def engine(self): return self.store.engine if self.store else (detect(self.url)[0] if self.url else None)

    def connect(self):
        if not self.url: self.problem = "DATABASE_URL is not set"; return
        try:
            if self.store is None: self.store = open_store(self.url)
            self.problem = self.store.check()
        except StoreUnavailable as exc:
            self.store, self.problem = None, str(exc)
        except ImportError as exc:
            self.store, self.problem = None, f"missing Python package for this database: {exc.name}. Run: pip install {exc.name.split('.')[0].replace('psycopg2', 'psycopg2-binary')}"

    def ensure(self):
        if self.ok or time.monotonic() - self._tried < 5: return
        self._tried = time.monotonic(); self.connect()

    def need(self):
        self.ensure()
        if not self.ok: raise StoreUnavailable(self.problem or "catalogue unavailable")
        return self.store
