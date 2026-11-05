"""Turn the dump's CSV, TSV or JSON Lines files into a SQLite or DuckDB file AniServe can serve.

  python load_dump.py anilist-db-dump-<tag>-csv.zip  --to sqlite  --out anilist.sqlite
  python load_dump.py anilist-db-dump-<tag>-tsv.zip  --to duckdb  --out anilist.duckdb
  python load_dump.py anilist-db-dump-<tag>-json.zip --to sqlite  --out anilist.sqlite
  python load_dump.py unzipped-folder/               --to sqlite  --out anilist.sqlite

The input is a release zip, a folder, or single files, named <schema>.<table>.csv|tsv|jsonl (as in the release).
The output has the same layout as the release's own .sqlite and .duckdb files, plus helpful indexes.
You do not need this for the .sqlite, .duckdb, Parquet or PostgreSQL files, which AniServe reads as they are.
Empty CSV fields are read as NULL.
"""
import argparse, csv, io, json, re, sys, zipfile
from datetime import datetime
from pathlib import Path

csv.field_size_limit(sys.maxsize)

# table -> ordered (column, kind). Kinds: int, bool, text, json (kept as JSON text), array (list of text), time.
TABLES = {
    "anilist_catalog": [("id", "int"), ("payload", "json"), ("search_text", "text"), ("status", "text"), ("season_year", "int"), ("season", "text"), ("format", "text"),
                        ("popularity", "int"), ("trending", "int"), ("mean_score", "int"), ("genres", "array"), ("tags", "array"), ("next_airing_at", "time"),
                        ("refresh_after", "time"), ("indexed_at", "time"), ("is_adult", "bool"), ("favourites", "int"), ("average_score", "int"),
                        ("media_updated_at", "int"), ("start_key", "int")],
    "anilist_detail_pages": [("id", "int"), ("kind", "text"), ("page", "int"), ("items", "json"), ("total", "int"), ("fetched_at", "time")],
    "anilist_index_state": [("key", "text"), ("value", "json"), ("updated_at", "time")],
    "anilist_missing_ids": [("id", "int"), ("checked_at", "time")],
}
INDEXES = ["CREATE INDEX IF NOT EXISTS anilist_catalog_status_idx ON anilist_catalog(status)", "CREATE INDEX IF NOT EXISTS anilist_catalog_year_idx ON anilist_catalog(season_year)",
           "CREATE INDEX IF NOT EXISTS anilist_catalog_pop_idx ON anilist_catalog(popularity)", "CREATE INDEX IF NOT EXISTS anilist_catalog_airing_idx ON anilist_catalog(next_airing_at)"]


def parse_pg_array(text):
    """Parse a PostgreSQL text array such as {Action,"Slice of Life",NULL} into a list of strings."""
    if text in (None, ""): return None
    t = text.strip()
    if not (t.startswith("{") and t.endswith("}")): raise ValueError(f"not a PostgreSQL array: {text[:40]}")
    out, i, n = [], 1, len(t) - 1
    while i < n:
        if t[i] == '"':
            i += 1; buf = []
            while t[i] != '"':
                if t[i] == "\\": i += 1
                buf.append(t[i]); i += 1
            out.append("".join(buf)); i += 1
        else:
            j = i
            while j < n and t[j] != ",": j += 1
            item = t[i:j]; out.append(None if item == "NULL" else item); i = j
        if i < n and t[i] == ",": i += 1
    return out


def parse_time(v):
    """'2026-11-02 05:00:00.5+00' (CSV) or '2026-11-02T05:00:00.5+00:00' (JSON) -> ISO text in UTC."""
    if v in (None, ""): return None
    v = re.sub(r"([+-]\d\d)$", r"\1:00", v.strip())
    d = datetime.fromisoformat(v)
    return d.astimezone(__import__("datetime").timezone.utc).isoformat() if d.tzinfo else d.isoformat()


def convert(value, kind, source):
    """-> Python value for the target. source is 'csv' (everything is text) or 'json' (already typed)."""
    if value is None or value == "": return None
    if kind == "int": return int(value)
    if kind == "bool": return value if isinstance(value, bool) else value.strip().lower() in ("t", "true", "1")
    if kind == "time": return parse_time(value)
    if kind == "array": return value if isinstance(value, list) else parse_pg_array(value)
    if kind == "json": return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return value if isinstance(value, str) else str(value)


def read_rows(name, ext, handle):
    """Yield dicts of raw values from one table file."""
    if ext == "jsonl":
        for line in handle:
            if line.strip(): yield json.loads(line)
    else:
        for row in csv.DictReader(handle, delimiter="\t" if ext == "tsv" else ","):
            yield row


def find_inputs(src):
    """-> {table: (ext, opener)} from a zip, a folder or a single file."""
    found = {}
    def consider(name, opener):
        m = re.match(r"(?:.*[/\\])?(?:[A-Za-z0-9_]+\.)?([A-Za-z0-9_]+)\.(csv|tsv|jsonl)$", name)
        if m and m[1] in TABLES: found[m[1]] = (m[2], opener)
    p = Path(src)
    if p.suffix.lower() == ".zip":
        z = zipfile.ZipFile(p)
        for n in z.namelist(): consider(n, lambda n=n: io.TextIOWrapper(z.open(n), encoding="utf-8", newline=""))
    elif p.is_dir():
        for f in sorted(p.rglob("*")): consider(str(f), lambda f=f: open(f, encoding="utf-8", newline=""))
    elif p.is_file():
        consider(str(p), lambda: open(p, encoding="utf-8", newline=""))
    return found


def load_sqlite(out, tables):
    import sqlite3
    Path(out).unlink(missing_ok=True)
    con = sqlite3.connect(out)
    ctype = {"int": "INTEGER", "bool": "INTEGER", "text": "TEXT", "json": "TEXT", "array": "TEXT", "time": "TEXT"}
    for name, (ext, opener) in tables.items():
        cols = TABLES[name]
        ddl = ", ".join('"%s" %s' % (c, ctype[k]) for c, k in cols)
        con.execute(f'CREATE TABLE "{name}" ({ddl})')
        with opener() as h:
            n = 0; batch = []
            for raw in read_rows(name, ext, h):
                row = []
                for c, k in cols:
                    v = convert(raw.get(c), k, ext)
                    row.append(json.dumps(v, ensure_ascii=False) if k == "array" and v is not None else (int(v) if isinstance(v, bool) else v))
                batch.append(row); n += 1
                if len(batch) >= 2000:
                    con.executemany(f'INSERT INTO "{name}" VALUES ({",".join("?" * len(cols))})', batch); batch = []
            if batch: con.executemany(f'INSERT INTO "{name}" VALUES ({",".join("?" * len(cols))})', batch)
        print(f"{name}: {n} rows", file=sys.stderr)
    for sql in INDEXES:
        if "anilist_catalog" in tables: con.execute(sql)
    con.commit(); con.close()


def load_duckdb(out, tables):
    import duckdb
    Path(out).unlink(missing_ok=True)
    con = duckdb.connect(out)
    con.execute('CREATE SCHEMA IF NOT EXISTS "public"')  # the release file keeps the PostgreSQL schema name
    ctype = {"int": "BIGINT", "bool": "BOOLEAN", "text": "VARCHAR", "json": "VARCHAR", "array": "VARCHAR[]", "time": "TIMESTAMPTZ"}
    for name, (ext, opener) in tables.items():
        cols = TABLES[name]
        ddl = ", ".join('"%s" %s' % (c, ctype[k]) for c, k in cols)
        con.execute(f'CREATE TABLE "public"."{name}" ({ddl})')
        with opener() as h:
            n = 0; batch = []
            for raw in read_rows(name, ext, h):
                batch.append([convert(raw.get(c), k, ext) for c, k in cols]); n += 1
                if len(batch) >= 2000:
                    con.executemany(f'INSERT INTO "public"."{name}" VALUES ({",".join("?" * len(cols))})', batch); batch = []
            if batch: con.executemany(f'INSERT INTO "public"."{name}" VALUES ({",".join("?" * len(cols))})', batch)
        print(f"{name}: {n} rows", file=sys.stderr)
    con.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="release zip (-csv.zip, -tsv.zip, -json.zip), a folder of unzipped files, or one file")
    ap.add_argument("--to", choices=("sqlite", "duckdb"), required=True)
    ap.add_argument("--out", required=True, help="file to create (replaced if it exists)")
    a = ap.parse_args()
    tables = find_inputs(a.input)
    if "anilist_catalog" not in tables:
        sys.exit("error: no anilist_catalog .csv, .tsv or .jsonl found in the input")
    (load_sqlite if a.to == "sqlite" else load_duckdb)(a.out, tables)
    print(f"wrote {a.out}. Serve it with DATABASE_URL={a.to}:///{a.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
