"""Kura: a read-only JSON API over an AniList Indexer PostgreSQL catalog.

Kura never talks to AniList and never writes to the database. Point it at the
database an AniList Indexer fills, and it serves the same Eiryui / Miruro
compatible routes as fast local JSON, plus /stats and /export.

Derived from AniList Indexer by JsmaAtom (MIT). See LICENSE.
"""
import asyncio, json, logging, math, os, re, threading, time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import psycopg2
import psycopg2.pool
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()
APP_NAME = "Kura"
APP_VERSION = "1.0.0"
LOG = logging.getLogger("kura")
LOG.setLevel(logging.INFO)

BASE_DIR = Path(__file__).resolve().parent
UI_DIR = BASE_DIR / "ui"


def env_flag(name, default):
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


STRICT = env_flag("API_STRICT_MIRURO_SHAPE", "true")
ENABLE_EXPORT = env_flag("KURA_ENABLE_EXPORT", "true")

# --------------------------------------------------------------------------- #
# Database (read-only)
# --------------------------------------------------------------------------- #
# Dead TCP connections are detected quickly (managed Postgres drops idle ones).
KEEPALIVE = {"keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10, "keepalives_count": 5, "connect_timeout": 15}
# Every session is read-only at the server, so a bug here can never modify the catalog.
READ_ONLY = "-c default_transaction_read_only=on -c statement_timeout=30000"


class Database:
    def __init__(self):
        self.url = os.getenv("DATABASE_URL", "")
        self.pool = None
        self.size = max(2, int(os.getenv("DATABASE_POOL_SIZE", "10")))
        self.slots = threading.BoundedSemaphore(self.size)
        self.schema_ok = False
        self.schema_problem = None
        self.used = {}

    def init(self):
        if not self.url:
            self.schema_problem = "DATABASE_URL is not set"
            return False
        self.pool = psycopg2.pool.ThreadedConnectionPool(1, self.size, self.url, options=READ_ONLY, **KEEPALIVE)
        self.check_schema()
        return True

    def ensure(self):
        """Recover from a database that was down at startup, or a schema the indexer had not created yet. Retried at most every 5 s."""
        if (self.pool and self.schema_ok) or time.monotonic() - getattr(self, "_tried", 0) < 5:
            return
        self._tried = time.monotonic()
        try:
            if not self.pool: self.init()
            else: self.check_schema()
        except Exception as exc:
            self.pool, self.schema_ok, self.schema_problem = None, False, str(exc)

    def check_schema(self):
        try:
            with self.cursor() as c:
                c.execute("SELECT to_regclass('public.anilist_catalog') IS NOT NULL")
                if not c.fetchone()[0]:
                    self.schema_ok, self.schema_problem = False, "table anilist_catalog not found; run an AniList Indexer against this database first"
                    return
                c.execute("SELECT count(*) FROM information_schema.columns WHERE table_name='anilist_catalog' AND column_name IN ('is_adult','start_key','average_score','favourites','media_updated_at')")
                if c.fetchone()[0] < 5:
                    self.schema_ok, self.schema_problem = False, "anilist_catalog is missing generated columns; upgrade the AniList Indexer (1.8+) so it migrates the schema"
                    return
            self.schema_ok, self.schema_problem = True, None
        except Exception as exc:
            self.schema_ok, self.schema_problem = False, str(exc)

    def _checkout(self):
        """A live connection. One idle for a while is pinged first, so a connection the server dropped is replaced instead of failing a request."""
        for _ in range(3):
            conn = self.pool.getconn()
            if conn.closed or time.monotonic() - self.used.get(id(conn), 0) > 20:
                try:
                    with conn.cursor() as cur: cur.execute("SELECT 1")
                    conn.rollback()
                except Exception:
                    self.pool.putconn(conn, close=True); continue
            self.used[id(conn)] = time.monotonic()
            return conn
        raise HTTPException(503, "database unreachable; retry shortly")

    @contextmanager
    def cursor(self, named=None):
        if not self.pool:
            raise HTTPException(503, "catalog database unavailable")
        if not self.slots.acquire(timeout=30):
            raise HTTPException(503, "timed out waiting for a database connection")
        conn = None
        try:
            try:
                conn = self._checkout()
                with conn.cursor(name=named) if named else conn.cursor() as cur:
                    yield cur
            except (psycopg2.OperationalError, psycopg2.InterfaceError) as exc:
                LOG.warning("database connection failed: %s", exc)
                if conn is not None: conn.close()  # putconn below discards it
                self.schema_ok = False; self.schema_problem = "database connection lost"
                raise HTTPException(503, "database connection lost; retry shortly") from exc
        finally:
            if conn is not None:
                try: conn.rollback()  # read-only: nothing to commit, just end the transaction
                except Exception: pass
                self.pool.putconn(conn, close=bool(conn.closed))
            self.slots.release()


db = Database()


def load_catalog():
    """Genre and tag lists shared with the Eiryui frontend, read from genres-tags.js."""
    src = (BASE_DIR / "genres-tags.js").read_text(encoding="utf-8")
    def grab(name):
        start = src.index(f"const {name} = ") + len(f"const {name} = ")
        return json.loads(src[start:src.index(";", start)])
    return grab("GENRES"), grab("TAGS")


GENRES, TAGS = load_catalog()

# --------------------------------------------------------------------------- #
# Record shaping: stored JSONB -> public JSON
# --------------------------------------------------------------------------- #
# Postgres JSONB stores object keys sorted by length, so records come back shuffled.
# ordered() restores one fixed, readable field order (the order Miruro's /info returns).
TOP_ORDER = ["id", "idMal", "title", "description", "coverImage", "bannerImage", "format", "season", "seasonYear", "episodes", "duration", "status", "averageScore", "meanScore", "popularity", "favourites", "trending", "genres", "tags", "source", "countryOfOrigin", "isAdult", "hashtag", "synonyms", "siteUrl", "trailer", "studios", "mainStudios", "updatedAt", "nextAiringEpisode", "startDate", "endDate", "characters", "staff", "relations", "recommendations", "externalLinks", "streamingEpisodes", "stats", "_extrasAt"]
_MEDIA_REF = ["id", "title", "coverImage", "bannerImage", "isAdult", "format", "episodes", "status", "meanScore", "averageScore", "popularity", "genres", "startDate"]
ORDER_BY_PATH = {
    "": TOP_ORDER, "studios": ["nodes"], "mainStudios": ["nodes"],
    "studios.nodes": ["id", "name", "isAnimationStudio", "siteUrl"], "mainStudios.nodes": ["name", "isAnimationStudio"],
    "tags": ["name", "rank", "isMediaSpoiler"], "relations": ["edges"], "relations.edges": ["relationType", "node"],
    "relations.edges.node": ["id", "title", "coverImage", "bannerImage", "isAdult", "format", "type", "status", "episodes", "chapters", "meanScore", "averageScore", "popularity", "startDate"],
    "recommendations": ["pageInfo", "nodes"], "recommendations.nodes": ["rating", "mediaRecommendation"], "recommendations.nodes.mediaRecommendation": _MEDIA_REF,
    "characters": ["pageInfo", "edges"], "characters.edges": ["role", "node", "voiceActors"], "characters.edges.node": ["id", "name", "image"], "characters.edges.voiceActors": ["id", "name", "image", "languageV2"],
    "staff": ["edges"], "staff.edges": ["role", "node"], "staff.edges.node": ["id", "name", "image"],
    "externalLinks": ["url", "site", "type"], "streamingEpisodes": ["title", "thumbnail", "url", "site"],
    "stats": ["scoreDistribution", "statusDistribution"], "stats.scoreDistribution": ["score", "amount"], "stats.statusDistribution": ["status", "amount"],
}
ORDER_BY_KEY = {
    "title": ["romaji", "english", "native"], "coverImage": ["large", "extraLarge", "medium", "color"], "trailer": ["id", "site", "thumbnail"],
    "nextAiringEpisode": ["episode", "airingAt", "timeUntilAiring"], "startDate": ["year", "month", "day"], "endDate": ["year", "month", "day"],
    "pageInfo": ["total", "currentPage", "hasNextPage", "perPage"], "name": ["full", "native", "userPreferred"], "image": ["large", "medium"],
}


def ordered(value, path=""):
    """Return value with every object's keys in the fixed order. Lists keep their order; unlisted keys follow the listed ones."""
    if isinstance(value, list):
        return [ordered(v, path) for v in value]
    if not isinstance(value, dict):
        return value
    spec = ORDER_BY_PATH.get(path) or ORDER_BY_KEY.get(path.rsplit(".", 1)[-1]) or []
    rank = {k: i for i, k in enumerate(spec)}
    keys = sorted(value, key=lambda k: rank.get(k, len(rank)))  # stable
    return {k: ordered(value[k], f"{path}.{k}" if path else k) for k in keys}


LIST_KEYS = ("id", "title", "bannerImage", "format", "season", "seasonYear", "episodes", "duration", "status", "averageScore", "meanScore", "popularity", "favourites", "genres", "tags", "source", "countryOfOrigin", "isAdult", "nextAiringEpisode", "startDate", "endDate")


def fresh_airing(x, now=None):
    """timeUntilAiring is relative to the moment of the crawl, so a stored value goes stale. Recompute it from airingAt."""
    a = x.get("nextAiringEpisode")
    if isinstance(a, dict) and a.get("airingAt") is not None:
        x["nextAiringEpisode"] = {**a, "timeUntilAiring": max(0, a["airingAt"] - (now or int(time.time())))}
    return x


def slim(x):
    """The slim list shape: no description, characters, relations or recommendations, and main studios only."""
    cover = x.get("coverImage") or {}
    main = x.get("mainStudios")
    if main is None:  # older record without main studios: animation studios are the closest match
        main = {"nodes": [{"name": m.get("name"), "isAnimationStudio": m.get("isAnimationStudio")} for m in (x.get("studios") or {}).get("nodes") or [] if m.get("isAnimationStudio")]}
    out = {k: x[k] for k in LIST_KEYS if k in x}
    out["coverImage"] = {k: cover[k] for k in (("large", "extraLarge") if STRICT else ("large", "extraLarge", "color")) if k in cover}
    out["studios"] = main
    return ordered(fresh_airing(out))


def _pick(d, keys):
    return {k: d[k] for k in keys if k in d}


def _node(n, keys, cover):
    if not isinstance(n, dict):
        return n
    out = _pick(n, keys)
    if isinstance(out.get("coverImage"), dict):
        out["coverImage"] = _pick(out["coverImage"], cover)
    return out


RELATION_INFO_KEYS = ("id", "title", "coverImage", "format", "type", "status", "episodes", "meanScore")
RELATION_ROUTE_KEYS = ("id", "title", "coverImage", "bannerImage", "format", "type", "status", "episodes", "chapters", "meanScore", "averageScore", "popularity", "startDate")
REC_INFO_KEYS = ("id", "title", "coverImage", "format", "episodes", "status", "meanScore", "averageScore")
REC_ROUTE_KEYS = ("id", "title", "coverImage", "bannerImage", "format", "episodes", "status", "meanScore", "averageScore", "popularity", "genres", "startDate")


def strict_edges(edges, keys, cover):
    return [{**e, "node": _node(e.get("node"), keys, cover)} for e in edges or []] if STRICT else edges


def strict_recs(nodes, keys, cover):
    return [{**r, "mediaRecommendation": _node(r.get("mediaRecommendation"), keys, cover)} for r in nodes or []] if STRICT else nodes


def strict_info(m):
    """Trim a stored record to the exact field set of Miruro's /info."""
    if not STRICT:
        return m
    m.pop("updatedAt", None)
    for e in (m.get("characters") or {}).get("edges") or []:
        n = e.get("node")
        if isinstance(n, dict):
            if isinstance(n.get("name"), dict): n["name"] = _pick(n["name"], ("full", "native"))
            if isinstance(n.get("image"), dict): n["image"] = _pick(n["image"], ("large",))
    if isinstance(m.get("relations"), dict):
        m["relations"]["edges"] = strict_edges(m["relations"].get("edges"), RELATION_INFO_KEYS, ("large",))
    if isinstance(m.get("recommendations"), dict):
        m["recommendations"]["nodes"] = strict_recs(m["recommendations"].get("nodes"), REC_INFO_KEYS, ("large",))
    return m


def clean(x):
    """Drop bookkeeping keys that are stored with a record but are not part of the public shape."""
    x.pop("mainStudios", None); x.pop("_extrasAt", None)
    return x


def _linked_nodes(x):
    for e in (x.get("relations") or {}).get("edges") or []:
        yield e.get("node") or {}
    for r in (x.get("recommendations") or {}).get("nodes") or []:
        yield r.get("mediaRecommendation") or {}


def hide_adult_links(items):
    """Drop adult titles from the relations and recommendations inside stored records."""
    ids = {n["id"] for x in items for n in _linked_nodes(x) if n.get("id")}
    adult = set()
    if ids:
        with db.cursor() as c:
            c.execute("SELECT id FROM anilist_catalog WHERE is_adult AND id = ANY(%s)", (list(ids),))
            adult = {r[0] for r in c.fetchall()}
    hidden = lambda n: bool(n) and bool(n.get("isAdult") or n.get("id") in adult)
    for x in items:
        if isinstance(x.get("relations"), dict):
            x["relations"]["edges"] = [e for e in x["relations"].get("edges") or [] if not hidden(e.get("node"))]
        if isinstance(x.get("recommendations"), dict):
            x["recommendations"]["nodes"] = [r for r in x["recommendations"].get("nodes") or [] if not hidden(r.get("mediaRecommendation"))]


def fill_colors(items):
    """Add coverImage.color to related and recommended titles stored before it was requested, from the color the catalog holds for them."""
    nodes = {}
    for x in items:
        for n in _linked_nodes(x):
            if n.get("id") and isinstance(n.get("coverImage"), dict) and "color" not in n["coverImage"]:
                nodes.setdefault(n["id"], []).append(n["coverImage"])
    if not nodes:
        return
    with db.cursor() as c:
        c.execute("SELECT id, payload#>>'{coverImage,color}' FROM anilist_catalog WHERE id = ANY(%s)", (list(nodes),))
        for i, color in c.fetchall():
            for cover in nodes[i]:
                cover["color"] = color


# --------------------------------------------------------------------------- #
# Queries
# --------------------------------------------------------------------------- #
def rows(where, params, order, page, n, adult=False, shape="full"):
    """shape: full (stored record), slim (list shape) or raw (stored record, adult links untouched)."""
    if not adult:
        where = [*where, "is_adult = false"]
    clause = " WHERE " + " AND ".join(where) if where else ""
    with db.cursor() as c:
        c.execute("SELECT count(*) FROM anilist_catalog" + clause, params)
        total = c.fetchone()[0]
        c.execute(f"SELECT payload FROM anilist_catalog{clause} ORDER BY {order} LIMIT %s OFFSET %s", [*params, n, (page - 1) * n])
        result = [x[0] for x in c.fetchall()]
    if shape == "slim":
        result = [slim(x) for x in result]
    elif shape == "full":
        if not adult: hide_adult_links(result)
        fill_colors(result)
    return {"page": page, "perPage": n, "total": total, "hasNextPage": page * n < total, "results": result}


def like(text):
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


SORT_COLUMNS = {"POPULARITY": "popularity", "SCORE": "average_score", "TRENDING": "trending", "START_DATE": "start_key", "FAVOURITES": "favourites", "UPDATED_AT": "media_updated_at"}
SORT_NAMES = [f"{k}_{d}" for k in (*SORT_COLUMNS, "ID") for d in ("DESC", "ASC")]


def sort_order(sort):
    """NAME_DESC is most first, NAME_ASC is least first. Missing values sort last; ties fall back to popularity, then id, so pages never overlap."""
    m = re.fullmatch(r"([A-Z_]+)_(ASC|DESC)", (sort or "").strip().upper())
    if not m or (m[1] != "ID" and m[1] not in SORT_COLUMNS):
        raise HTTPException(400, f"Unknown sort '{sort}'. Use one of: {', '.join(SORT_NAMES)}.")
    name, direction = m[1], m[2]
    if name == "ID":
        return f"id {direction}"
    tie = "" if name == "POPULARITY" else "popularity DESC NULLS LAST,"
    return f"{SORT_COLUMNS[name]} {direction} NULLS LAST,{tie}id ASC"


async def query(search=None, genre=None, tag=None, year=None, season=None, format=None, status=None, sort="POPULARITY_DESC", page=1, n=20, adult=False):
    where, p = [], []
    if search and search.strip():
        # Every word must appear somewhere in the titles/synonyms, in any order.
        for word in re.findall(r"\w+", search) or [search.strip()]:
            where.append("search_text ILIKE %s"); p.append(like(word))
    for col, val in (("season_year =", year), ("season =", season.upper() if season else None), ("format =", format.upper() if format else None), ("status =", status.upper() if status else None)):
        if val is not None:
            where.append(col + " %s"); p.append(val)
    if genre: where.append("genres @> %s"); p.append(genre if isinstance(genre, list) else [genre])
    if tag: where.append("tags @> %s"); p.append(tag if isinstance(tag, list) else [tag])
    order = sort_order(sort)
    return await asyncio.to_thread(rows, where, p, order, page, n, adult, "slim")


# --------------------------------------------------------------------------- #
# App, rate limiter
# --------------------------------------------------------------------------- #
class ApiRateLimiter:
    """Small in-memory fixed-window limiter per client IP."""
    def __init__(self):
        self.rpm = int(os.getenv("API_RATE_LIMIT_RPM", "120"))
        self.window = max(1, int(os.getenv("API_RATE_LIMIT_WINDOW_SECONDS", "60")))
        self.limit = 0 if self.rpm <= 0 else max(1, math.ceil(self.rpm * self.window / 60))
        self.trust_proxy = env_flag("API_RATE_LIMIT_TRUST_PROXY", "false")
        self.clients = {}
        self.lock = asyncio.Lock()

    def client_ip(self, request: Request):
        if self.trust_proxy:
            forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            if forwarded: return forwarded
        return request.client.host if request.client else "unknown"

    async def check(self, request: Request):
        if self.limit <= 0 or request.method == "OPTIONS" or request.url.path.startswith("/assets/") or request.url.path == "/favicon.ico":
            return None
        now = time.monotonic(); ip = self.client_ip(request)
        async with self.lock:
            started, count = self.clients.get(ip, (now, 0))
            if now - started >= self.window: started, count = now, 0
            count += 1
            self.clients[ip] = (started, count)
            if len(self.clients) > 4096:
                self.clients = {k: v for k, v in self.clients.items() if now - v[0] < self.window}
            return count <= self.limit, max(0, self.limit - count), max(1, int(self.window - (now - started)))


api_limiter = ApiRateLimiter()
app = FastAPI(title=APP_NAME, version=APP_VERSION, description="Read-only JSON API over an AniList Indexer catalog.", license_info={"name": "MIT", "identifier": "MIT"})
app.mount("/assets", StaticFiles(directory=UI_DIR / "assets"), name="assets")
origins = [x.strip() for x in os.getenv("ALLOWED_ORIGINS", "*").split(",")]
# CORS is wide open by default and the API is read-only and cookie-less, so credentials stay off.
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False, allow_methods=["GET", "OPTIONS"], allow_headers=["*"], expose_headers=["X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset", "X-Kura-Ms"])


@app.middleware("http")
async def guard(request: Request, call_next):
    started = time.perf_counter()
    result = await api_limiter.check(request)
    headers = {}
    if result:
        allowed, remaining, retry_after = result
        headers = {"X-RateLimit-Limit": str(api_limiter.limit), "X-RateLimit-Remaining": str(remaining), "X-RateLimit-Reset": str(retry_after)}
        if not allowed:
            headers["Retry-After"] = str(retry_after)
            return JSONResponse(status_code=429, headers=headers, content={"detail": "API rate limit exceeded. Try again after the requested window.", "retry_after": retry_after})
    try:
        response = await call_next(request)
    except Exception:
        LOG.exception("Unhandled API error: %s %s", request.method, request.url.path)
        raise
    response.headers.update(headers)
    response.headers["X-Kura-Ms"] = f"{(time.perf_counter() - started) * 1000:.1f}"
    return response


@app.on_event("startup")
async def start():
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=int(os.getenv("THREAD_POOL_SIZE", "32")), thread_name_prefix="kura"))
    try:
        await asyncio.to_thread(db.init)
    except Exception as exc:
        db.schema_problem = str(exc)
        LOG.exception("Database startup failed")
    if db.schema_problem:
        LOG.error("Kura started without a usable catalog: %s", db.schema_problem)


Adult = Annotated[bool, Query(alias="isAdult", description="true includes adult titles alongside the rest; false or omitted hides them.")]
PageQ = Annotated[int, Query(ge=1, description="Page number, starting at 1.")]


def PerPage(default, maximum=50):
    return Annotated[int, Query(ge=1, le=maximum, description=f"Results per page, 1 to {maximum}.")]


def need_db():
    db.ensure()
    if not db.pool or not db.schema_ok:
        raise HTTPException(503, db.schema_problem or "catalog database unavailable")


# --------------------------------------------------------------------------- #
# Site routes
# --------------------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def home():
    html = await asyncio.to_thread((UI_DIR / "index.html").read_text, encoding="utf-8")
    for name in ("kura.js", "kura.css"):  # version asset URLs by mtime so browsers never keep a stale copy
        try: stamp = int((UI_DIR / "assets" / name).stat().st_mtime)
        except OSError: continue
        html = html.replace(f"/assets/{name}", f"/assets/{name}?v={stamp}")
    return HTMLResponse(html.replace("{{VERSION}}", APP_VERSION), headers={"Cache-Control": "no-cache"})


@app.get("/favicon.ico", include_in_schema=False)
@app.get("/favicon.svg", include_in_schema=False)
async def favicon():
    return FileResponse(UI_DIR / "assets" / "favicon.svg", media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/swagger", include_in_schema=False)
async def swagger_docs():
    return get_swagger_ui_html(openapi_url=app.openapi_url, title="Kura - Swagger", swagger_favicon_url="/favicon.svg")


@app.get("/license", response_class=PlainTextResponse, include_in_schema=False)
async def license_text():
    return PlainTextResponse(await asyncio.to_thread((BASE_DIR / "LICENSE").read_text, encoding="utf-8"))


@app.get("/health")
async def health():
    await asyncio.to_thread(db.ensure)
    out = {"status": "online" if db.schema_ok else "degraded", "name": APP_NAME, "version": APP_VERSION, "database": "connected" if db.schema_ok else "unavailable", "mode": "read-only"}
    if not db.schema_ok and db.schema_problem:
        out["problem"] = db.schema_problem
    return out


# --------------------------------------------------------------------------- #
# API routes: lists and search
# --------------------------------------------------------------------------- #
@app.get("/search")
async def search(query_: str = Query(..., alias="query"), page: PageQ = 1, per_page: PerPage(20) = 20, is_adult: Adult = False):
    need_db(); return await query(search=query_, page=page, n=per_page, adult=is_adult)


@app.get("/multi-search")
async def multi_search(q: str | None = Query(None, min_length=1), genre: list[str] | None = Query(None), tag: list[str] | None = Query(None), year: int | None = None, season: str | None = None, format: str | None = None, status: str | None = None, sort: str = "POPULARITY_DESC", page: PageQ = 1, per_page: PerPage(30) = 30, is_adult: Adult = False):
    need_db(); return await query(q, genre, tag, year, season, format, status, sort, page, per_page, adult=is_adult)


@app.get("/filter")
async def filter(q: str | None = Query(None, min_length=1), genre: list[str] | None = Query(None), tag: list[str] | None = Query(None), year: int | None = None, season: str | None = None, format: str | None = None, status: str | None = None, sort: str = "POPULARITY_DESC", page: PageQ = 1, per_page: PerPage(20) = 20, is_adult: Adult = False):
    need_db(); return await query(q, genre, tag, year, season, format, status, sort, page, per_page, adult=is_adult)


@app.get("/suggestions")
async def suggestions(query_: str = Query(..., alias="query", min_length=1), is_adult: Adult = False):
    need_db()
    data = await query(search=query_, page=1, n=8, adult=is_adult)
    out = []
    for x in data["results"]:
        t = x.get("title") or {}
        out.append({"id": x["id"], "title": t.get("english") or t.get("romaji"), "title_romaji": t.get("romaji"), "poster": (x.get("coverImage") or {}).get("large"),
                    **({} if STRICT else {"coverImage": x.get("coverImage") or {}}), "format": x.get("format"), "status": x.get("status"), "year": (x.get("startDate") or {}).get("year"), "episodes": x.get("episodes")})
    return {"suggestions": out}


@app.get("/trending")
async def trending(page: PageQ = 1, per_page: PerPage(20) = 20, is_adult: Adult = False):
    need_db(); return await query(sort="TRENDING_DESC", page=page, n=per_page, adult=is_adult)


@app.get("/popular")
async def popular(page: PageQ = 1, per_page: PerPage(20) = 20, is_adult: Adult = False):
    need_db(); return await query(page=page, n=per_page, adult=is_adult)


@app.get("/recent")
async def recent(page: PageQ = 1, per_page: PerPage(20) = 20, is_adult: Adult = False):
    need_db(); return await query(status="RELEASING", sort="START_DATE_DESC", page=page, n=per_page, adult=is_adult)


@app.get("/upcoming")
async def upcoming(page: PageQ = 1, per_page: PerPage(20) = 20, is_adult: Adult = False):
    need_db(); return await query(status="NOT_YET_RELEASED", page=page, n=per_page, adult=is_adult)


@app.get("/spotlight")
async def spotlight(is_adult: Adult = False):
    need_db(); return {"results": (await query(sort="TRENDING_DESC", n=10, adult=is_adult))["results"]}


@app.get("/genres")
async def genres(is_adult: Adult = False):
    """The fixed frontend-supported genre list. Hentai only with isAdult=true."""
    return {"results": [{"name": g, "isAdult": g == "Hentai"} for g in GENRES if is_adult or g != "Hentai"]}


@app.get("/tags")
async def tags(is_adult: Adult = False):
    """The fixed frontend-supported tag list."""
    return {"results": [{"name": t, "isAdult": False} for t in TAGS]}


def schedule_item(x, now):
    """Schedule entry: slim list fields plus next_episode / airingAt / timeUntilAiring."""
    a = x.get("nextAiringEpisode") or {}; airing = a.get("airingAt")
    item = slim(x)
    if not STRICT:
        item.update({"color": (x.get("coverImage") or {}).get("color"), "banner": x.get("bannerImage"), "synopsis": x.get("description")})
    item.update({"next_episode": a.get("episode"), "airingAt": airing, "timeUntilAiring": max(0, (airing or now) - now)})
    return item


@app.get("/schedule")
async def schedule(page: PageQ = 1, per_page: PerPage(20) = 20, is_adult: Adult = False):
    """Upcoming airings, soonest first: one entry per title for its next episode."""
    need_db()
    data = await asyncio.to_thread(rows, ["next_airing_at > now()"], [], "next_airing_at ASC,id", page, per_page, is_adult, "raw")
    now = int(time.time())
    data["results"] = [schedule_item(x, now) for x in data["results"]]
    return data


@app.get("/list")
async def list_all(page: int = Query(1, ge=1), per_page: int = Query(100, ge=1, le=500), ids_only: bool = False, is_adult: Adult = False):
    """Every indexed title with its full stored record, ordered by id. Adult titles only when isAdult=true."""
    need_db()
    def get():
        clause = "" if is_adult else " WHERE is_adult = false"
        with db.cursor() as c:
            c.execute("SELECT count(*) FROM anilist_catalog" + clause); total = c.fetchone()[0]
            c.execute("SELECT id" + ("" if ids_only else ", payload") + " FROM anilist_catalog" + clause + " ORDER BY id LIMIT %s OFFSET %s", [per_page, (page - 1) * per_page])
            result = [r[0] for r in c.fetchall()] if ids_only else [r[1] for r in c.fetchall()]
        if not ids_only:
            if not is_adult: hide_adult_links(result)
            fill_colors(result); result = [ordered(fresh_airing(clean(x))) for x in result]
        return {"page": page, "perPage": per_page, "total": total, "hasNextPage": page * per_page < total, "results": result}
    return await asyncio.to_thread(get)


# --------------------------------------------------------------------------- #
# API routes: detail pages (stored data only, AniList is never contacted)
# --------------------------------------------------------------------------- #
def get_media(anilist_id, adult, shape="full"):
    data = rows(["id = %s"], [anilist_id], "id", 1, 1, adult, shape)
    if not data["results"]:
        raise HTTPException(404, "Anime not found")
    return data["results"][0]


INFO_RECS = 10  # /info lists 10 recommendations; /anime/{id}/recommendations has the rest


def finish_info(media, is_adult):
    rc = media.get("recommendations")
    if isinstance(rc, dict):
        rc["nodes"] = (rc.get("nodes") or [])[:INFO_RECS]
    if not is_adult: hide_adult_links([media])
    fill_colors([media])
    return media


@app.get("/info/{anilist_id}")
async def info(anilist_id: int, is_adult: Adult = False):
    """The full stored record. staff, externalLinks, streamingEpisodes and stats appear only if the indexer side has already stored them."""
    need_db()
    media = await asyncio.to_thread(lambda: finish_info(get_media(anilist_id, is_adult, "raw"), is_adult))
    clean(media); fresh_airing(media)
    for key in ("characters", "recommendations"):
        if isinstance(media.get(key), dict): media[key].pop("pageInfo", None)
    for e in (media.get("characters") or {}).get("edges") or []:
        e["voiceActors"] = [v for v in e.get("voiceActors") or [] if v.get("languageV2") == "Japanese"]  # /info lists Japanese cast only
    return ordered(strict_info(media))


BLOCK = 25  # characters / recommendations are stored in blocks of 25 (block 1 lives in the catalog record)


def read_blocks(anilist_id, kind, first, last):
    with db.cursor() as c:
        c.execute("SELECT page, items FROM anilist_detail_pages WHERE id=%s AND kind=%s AND page BETWEEN %s AND %s ORDER BY page", (anilist_id, kind, first, last))
        return dict(c.fetchall())


def detail_window(media, kind, key, lo, hi):
    """Items lo..hi (0-based, hi exclusive) from the record plus any stored extra blocks. Stops at the first block that was never stored."""
    rec = media.get(kind) or {}; stored = rec.get(key) or []
    total = (rec.get("pageInfo") or {}).get("total")
    if total is None: total = len(stored)
    hi = min(hi, total)
    if lo >= hi: return [], total
    if hi <= len(stored): return stored[lo:hi], total
    first, last = lo // BLOCK + 1, (hi - 1) // BLOCK + 1
    try: have = read_blocks(media["id"], kind, max(first, 2), last) if last >= 2 else {}
    except Exception:  # the indexer has not created anilist_detail_pages yet
        have = {}
    out = []
    for p in range(first, last + 1):
        items = stored[:BLOCK] if p == 1 and len(stored) >= min(BLOCK, total) else have.get(p)
        if items is None: break  # a gap: never stitch around it
        out += items
    return out[lo - (first - 1) * BLOCK:hi - (first - 1) * BLOCK], total


@app.get("/anime/{anilist_id}/characters")
async def chars(anilist_id: int, page: PageQ = 1, per_page: PerPage(25) = 25, is_adult: Adult = False):
    need_db()
    def get():
        media = get_media(anilist_id, is_adult)
        lo, hi = (page - 1) * per_page, page * per_page
        edges, total = detail_window(media, "characters", "edges", lo, hi)
        return {"page": page, "perPage": per_page, "total": total, "hasNextPage": hi < total, "characters": ordered(edges, "characters.edges")}
    return await asyncio.to_thread(get)


@app.get("/anime/{anilist_id}/relations")
async def relations(anilist_id: int, is_adult: Adult = False):
    need_db()
    media = await asyncio.to_thread(get_media, anilist_id, is_adult)
    title = _pick(media["title"], ("romaji", "english")) if STRICT else media["title"]
    return {"id": media["id"], "title": ordered(title, "title"), "relations": ordered(strict_edges((media.get("relations") or {}).get("edges", []), RELATION_ROUTE_KEYS, ("large",)), "relations.edges")}


@app.get("/anime/{anilist_id}/recommendations")
async def recommendations(anilist_id: int, page: PageQ = 1, per_page: PerPage(10, 25) = 10, is_adult: Adult = False):
    need_db()
    def get():
        media = get_media(anilist_id, is_adult, "raw")  # unfiltered, so offsets match AniList's
        lo, hi = (page - 1) * per_page, page * per_page
        nodes, total = detail_window(media, "recommendations", "nodes", lo, hi)
        holder = {"recommendations": {"nodes": nodes}}
        if not is_adult: hide_adult_links([holder])
        fill_colors([holder])
        shown = ordered(strict_recs(holder["recommendations"]["nodes"], REC_ROUTE_KEYS, ("large", "extraLarge")), "recommendations.nodes")
        return {"page": page, "perPage": per_page, "total": total, "hasNextPage": hi < total, "recommendations": shown}
    return await asyncio.to_thread(get)


# --------------------------------------------------------------------------- #
# Kura extras: /stats and /export
# --------------------------------------------------------------------------- #
_stats_cache = {}


def build_stats(adult):
    where = "" if adult else " WHERE is_adult = false"
    def group(c, expr, extra=""):
        c.execute(f"SELECT {expr} AS k, count(*) FROM anilist_catalog{where}{extra} GROUP BY 1 ORDER BY 2 DESC")
        return [{"key": k, "count": n} for k, n in c.fetchall()]
    with db.cursor() as c:
        c.execute(f"SELECT count(*), max(indexed_at), count(*) FILTER (WHERE next_airing_at > now()), min(season_year), max(season_year) FROM anilist_catalog{where}")
        total, last, airing, y0, y1 = c.fetchone()
        decades = group(c, "(season_year/10)*10", (" AND" if where else " WHERE") + " season_year IS NOT NULL")
        decades.sort(key=lambda d: d["key"])
        c.execute(f"SELECT g, count(*) FROM (SELECT unnest(genres) AS g FROM anilist_catalog{where}) s GROUP BY 1 ORDER BY 2 DESC LIMIT 12")
        top_genres = [{"key": k, "count": n} for k, n in c.fetchall()]
        by_status, by_format = group(c, "status"), group(c, "format")
        try:
            c.execute("SELECT key, updated_at FROM anilist_index_state ORDER BY key"); jobs = {k: u for k, u in c.fetchall()}
        except Exception:
            c.connection.rollback(); jobs = {}
    return {"entries": total, "airing": airing, "firstYear": y0, "lastYear": y1, "lastIndexedAt": last, "status": by_status, "format": by_format, "decades": decades, "genres": top_genres, "indexerJobs": jobs}


@app.get("/stats")
async def stats(is_adult: Adult = False):
    """Catalog ledger: entry counts by status, format, decade and genre, plus when the indexer last ran each job. Cached for 30 s."""
    need_db()
    hit = _stats_cache.get(is_adult)
    if hit and time.monotonic() - hit[0] < 30:
        return hit[1]
    data = await asyncio.to_thread(build_stats, is_adult)
    _stats_cache[is_adult] = (time.monotonic(), data)
    return data


_export_running = threading.Event()  # one full export at a time keeps the connection pool free for the API


def export_lines(adult, fmt, batch=200):
    """Yield the whole catalog as NDJSON (one record per line) or as one JSON array, streamed through a server-side cursor."""
    dumps = lambda x: json.dumps(x, ensure_ascii=False, separators=(",", ":"), default=str)
    clause = "" if adult else " WHERE is_adult = false"
    _export_running.set()
    try:
        yield from _export_body(adult, fmt, batch, clause, dumps)
    finally:
        _export_running.clear()


def _export_body(adult, fmt, batch, clause, dumps):
    first = True
    if fmt == "json": yield "["
    with db.cursor(named="kura_export") as c:
        c.itersize = batch
        c.execute("SELECT payload FROM anilist_catalog" + clause + " ORDER BY id")
        while True:
            chunk = c.fetchmany(batch)
            if not chunk: break
            items = [r[0] for r in chunk]
            if not adult: hide_adult_links(items)
            fill_colors(items)
            for x in items:
                rec = dumps(ordered(fresh_airing(clean(x))))
                if fmt == "json":
                    yield ("" if first else ",") + rec; first = False
                else:
                    yield rec + "\n"
    if fmt == "json": yield "]"


@app.get("/export")
async def export(format: str = Query("ndjson", pattern="^(ndjson|json)$"), is_adult: Adult = False):
    """Stream the entire catalog, full records ordered by id. ndjson (default) is one JSON object per line; json is a single array."""
    if not ENABLE_EXPORT: raise HTTPException(404, "Export is disabled (KURA_ENABLE_EXPORT=false)")
    need_db()
    if _export_running.is_set(): raise HTTPException(429, "Another export is already running; try again shortly")
    media_type = "application/x-ndjson" if format == "ndjson" else "application/json"
    return StreamingResponse(export_lines(is_adult, format), media_type=media_type, headers={"Content-Disposition": f'attachment; filename="kura-catalog.{ "ndjson" if format == "ndjson" else "json" }"'})
