"""AniServe: a read-only REST API that serves AniList metadata straight from a PostgreSQL database.

AniServe is a read-only PostgreSQL -> JSON service (Eiryui / Miruro-compatible routes and response shapes).
It never talks to AniList and never writes to the database: point DATABASE_URL at a database restored from the AniList database dump
(or any process that keeps the same tables, see schema.sql) and every route answers from it.

Made by AziF. Released under the MIT License (see LICENSE).
"""
import asyncio, json, logging, math, os, re, threading, time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Annotated

import psycopg2
import psycopg2.errors
import psycopg2.pool
from brotli_asgi import BrotliMiddleware
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()
ENVIRONMENT = os.getenv("APP_ENVIRONMENT", "production").strip().lower()
if ENVIRONMENT not in {"development", "production"}:
    ENVIRONMENT = "production"
IS_DEVELOPMENT = ENVIRONMENT == "development"
LOG = logging.getLogger("aniserve")
LOG.setLevel(logging.INFO)
if IS_DEVELOPMENT and not LOG.handlers:
    try:
        log_dir = Path(__file__).resolve().parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        error_handler = logging.FileHandler(log_dir / "error.txt", encoding="utf-8")
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        LOG.addHandler(error_handler)
    except OSError: pass  # read-only filesystem: errors still go to stdout logs

# AniList serves characters / recommendations in pages of 25, and they are stored in blocks of 25: block 1 on the record itself,
# blocks 2.. in anilist_detail_pages. The API reads them back with the same arithmetic.
BLOCK = 25
DETAIL_KEYS = {"characters": "edges", "recommendations": "nodes"}
EXTRA_KEYS = ("staff", "externalLinks", "streamingEpisodes", "stats")
# What a title reports for the extras until they have been stored for it (so clients can read the keys unconditionally).
EXTRA_EMPTY = {"staff": {"edges": []}, "externalLinks": [], "streamingEpisodes": [], "stats": {"scoreDistribution": [], "statusDistribution": []}}

# Detect dead TCP connections quickly (managed Postgres drops idle ones).
KEEPALIVE = {"keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10, "keepalives_count": 5, "connect_timeout": 15}

class Catalog:
    """Read-only connection pool over the anilist_catalog / anilist_detail_pages tables. AniServe never writes: every session is read-only."""
    def __init__(self):
        self.url = os.getenv("DATABASE_URL", "")
        self.pool = None
        self.pool_size = max(2, int(os.getenv("DATABASE_POOL_SIZE", "10")))
        self.slots = threading.BoundedSemaphore(self.pool_size)
        self.used = {}
        self.last_error = None

    @contextmanager
    def cursor(self):
        if not self.pool: raise RuntimeError("catalog database unavailable; set DATABASE_URL")
        # Wait for a free connection instead of failing: psycopg2 raises PoolError the moment the pool is exhausted.
        if not self.slots.acquire(timeout=30): raise RuntimeError("timed out waiting for a database connection")
        try:
            conn = self._checkout()
            try:
                with conn.cursor() as cur: yield cur
                conn.commit()
            except Exception:
                try: conn.rollback()
                except Exception: pass
                raise
            finally:
                # A connection the server dropped (idle timeouts, restarts) is closed, not returned, so it is replaced on the next use.
                self.pool.putconn(conn, close=bool(conn.closed))
        finally: self.slots.release()
    def _checkout(self):
        """Get a live connection; one idle for a while is pinged first so a
        connection the server dropped is replaced instead of failing a request."""
        for _ in range(3):
            conn = self.pool.getconn()
            if time.monotonic() - self.used.get(id(conn), 0) > 20:
                try:
                    with conn.cursor() as cur: cur.execute("SELECT 1")
                    conn.rollback()
                except Exception:
                    self.pool.putconn(conn, close=True); continue
            self.used[id(conn)] = time.monotonic()
            conn.readonly = True  # BEGIN READ ONLY on every transaction (works through PgBouncer too, unlike a startup option)
            return conn
        conn = self.pool.getconn(); conn.readonly = True
        return conn
    def init(self):
        """Open the pool. Returns False when DATABASE_URL is not set. Nothing is created or changed in the database."""
        if not self.url: return False
        self.pool = psycopg2.pool.ThreadedConnectionPool(1, self.pool_size, self.url, **KEEPALIVE)
        with self.cursor() as cur: cur.execute("SELECT 1")  # fail at startup, not on the first request, if the database is unreachable
        return True
    def close(self):
        if self.pool:
            try: self.pool.closeall()
            except Exception: pass
            self.pool = None

class ApiRateLimiter:
    """Small in-memory fixed-window limiter for public frontend requests."""
    def __init__(self):
        self.rpm = int(os.getenv("API_RATE_LIMIT_RPM", "120"))
        self.window = max(1, int(os.getenv("API_RATE_LIMIT_WINDOW_SECONDS", "60")))
        # Keep RPM meaningful when a shorter/longer fixed window is selected.
        self.limit = 0 if self.rpm <= 0 else max(1, math.ceil(self.rpm * self.window / 60))
        self.trust_proxy = os.getenv("API_RATE_LIMIT_TRUST_PROXY", "false").lower() in {"1", "true", "yes", "on"}
        self.clients = {}
        self.lock = asyncio.Lock()
    def client_ip(self, request: Request):
        if self.trust_proxy:
            forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            if forwarded: return forwarded
        return request.client.host if request.client else "unknown"
    async def check(self, request: Request):
        if self.limit <= 0 or request.method == "OPTIONS" or request.url.path.startswith("/assets/") or request.url.path == "/favicon.ico": return None
        now = time.monotonic(); ip = self.client_ip(request)
        async with self.lock:
            started, count = self.clients.get(ip, (now, 0))
            if now - started >= self.window:
                started, count = now, 0
            count += 1
            self.clients[ip] = (started, count)
            # Periodically discard expired entries so an exposed API cannot
            # retain every IP address it has ever seen.
            if len(self.clients) > 4096:
                self.clients = {key: value for key, value in self.clients.items() if now - value[0] < self.window}
            remaining = max(0, self.limit - count)
            retry_after = max(1, int(self.window - (now - started)))
            return count <= self.limit, remaining, retry_after

APP_VERSION = "1.6.3"
BASE_DIR = Path(__file__).resolve().parent
UI_DIR = BASE_DIR / "ui"
CATALOG_FILE = BASE_DIR / "genres-tags.js"
def load_catalog():
    """Genre and tag lists shared with the Eiryui frontend, read from genres-tags.js."""
    src = CATALOG_FILE.read_text(encoding="utf-8")
    def grab(name):
        start = src.index(f"const {name} = ") + len(f"const {name} = ")
        return json.loads(src[start:src.index(";", start)])
    return grab("GENRES"), grab("TAGS")
GENRES, TAGS = load_catalog()
LICENSE_FILE = BASE_DIR / "LICENSE"
catalog=Catalog()
async def connect_once():
    """True once the pool is open. False when DATABASE_URL is not set or the database cannot be reached (last_error says why)."""
    try:
        if not await asyncio.to_thread(catalog.init):
            LOG.error("Catalog disabled because DATABASE_URL is not configured"); return False
        catalog.last_error = None; return True
    except asyncio.CancelledError: raise
    except Exception as exc:
        catalog.last_error = str(exc); catalog.close()
        LOG.error("Catalog database unreachable: %s", (str(exc).strip().splitlines() or [exc.__class__.__name__])[0]); return False
async def reconnect_loop():
    """The database was not reachable at startup (a container starting before Postgres, a restart): keep trying with a growing
    delay instead of answering 503 until someone restarts the service."""
    delay = 2
    while True:
        await asyncio.sleep(delay)
        if await connect_once(): return
        delay = min(delay * 2, 30)
@asynccontextmanager
async def lifespan(_app):
    # The default executor is only cpu+4 threads (5 on a 1-CPU host). Every request does its blocking database work in a worker
    # thread, so a bigger pool keeps /health and the landing page responsive while the API is busy.
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=int(os.getenv("THREAD_POOL_SIZE","32")),thread_name_prefix="api"))
    task = None
    if not catalog.url: LOG.error("Catalog disabled because DATABASE_URL is not configured")
    elif not await connect_once(): task = asyncio.create_task(reconnect_loop())
    try: yield
    finally:
        if task: task.cancel()
        catalog.close()
app=FastAPI(title="AniServe",version=APP_VERSION,license_info={"name":"MIT","identifier":"MIT"},lifespan=lifespan)
app.mount("/assets", StaticFiles(directory=UI_DIR / "assets"), name="assets")
api_limiter = ApiRateLimiter()
origins=[x.strip() for x in os.getenv("ALLOWED_ORIGINS","*").split(",")]

@app.middleware("http")
async def limit_frontend_requests(request: Request, call_next):
    result = await api_limiter.check(request)
    if result:
        allowed, remaining, retry_after = result
        headers = {
            "X-RateLimit-Limit": str(api_limiter.limit),
            "X-RateLimit-Remaining": str(remaining),
            "X-RateLimit-Reset": str(retry_after),
        }
        if not allowed:
            headers["Retry-After"] = str(retry_after)
            return JSONResponse(status_code=429, headers=headers, content={
                "detail": "API rate limit exceeded. Try again after the requested window.",
                "retry_after": retry_after,
            })
        try:
            response = await call_next(request)
        except Exception:
            LOG.exception("Unhandled API error: %s %s", request.method, request.url.path)
            raise
        response.headers.update(headers)
        return response
    try:
        return await call_next(request)
    except Exception:
        LOG.exception("Unhandled API error: %s %s", request.method, request.url.path)
        raise
# Middleware order: the last one added is the outermost. CORS goes last so that every response, including the 429 from the limiter
# above and unhandled errors, carries CORS headers; otherwise a browser reports a rate-limited call as a CORS failure.
app.add_middleware(BrotliMiddleware,quality=4,minimum_size=500,gzip_fallback=True)  # br preferred, gzip fallback, plain otherwise
app.add_middleware(CORSMiddleware,allow_origins=origins,allow_credentials=True,allow_methods=["*"],allow_headers=["*"],
                   expose_headers=["Retry-After","X-RateLimit-Limit","X-RateLimit-Remaining","X-RateLimit-Reset","X-Index-Status"])  # browsers can read these

# AniServe never creates tables, so a database that has not been filled yet or is unreachable answers with a clear 503 instead of a bare 500.
@app.exception_handler(psycopg2.errors.UndefinedTable)
async def tables_missing(request: Request, exc: Exception):
    LOG.error("Catalog tables are missing: %s", str(exc).strip().splitlines()[0])
    return JSONResponse(status_code=503, content={"detail": "catalog tables missing; restore the AniList database dump or load schema.sql"})
@app.exception_handler(psycopg2.OperationalError)
@app.exception_handler(psycopg2.InterfaceError)
async def database_down(request: Request, exc: Exception):
    LOG.error("Catalog database error: %s", str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__)
    return JSONResponse(status_code=503, content={"detail": "catalog database unavailable"})

Adult=Annotated[bool,Query(alias="isAdult",description="true includes adult titles alongside the rest; false or omitted hides them.")]

def hide_adult_links(items):
    """Drop adult titles from the relations and recommendations inside stored records."""
    ids=set()
    for x in items:
        for e in (x.get("relations")or{}).get("edges")or[]:
            if (e.get("node")or{}).get("id"):ids.add(e["node"]["id"])
        for r in (x.get("recommendations")or{}).get("nodes")or[]:
            if (r.get("mediaRecommendation")or{}).get("id"):ids.add(r["mediaRecommendation"]["id"])
    adult=set()
    if ids:
        with catalog.cursor() as c:
            c.execute("SELECT id FROM anilist_catalog WHERE is_adult AND id = ANY(%s)",(list(ids),)); adult={r[0] for r in c.fetchall()}
    hidden=lambda n:bool(n) and bool(n.get("isAdult") or n.get("id") in adult)
    for x in items:
        if isinstance(x.get("relations"),dict):x["relations"]["edges"]=[e for e in x["relations"].get("edges")or[] if not hidden(e.get("node"))]
        if isinstance(x.get("recommendations"),dict):x["recommendations"]["nodes"]=[r for r in x["recommendations"].get("nodes")or[] if not hidden(r.get("mediaRecommendation"))]

# Postgres JSONB stores object keys sorted by length, so records come back with shuffled fields.
# ordered() puts every record back into one fixed, readable field order (the order Miruro's /info query returns them in).
TOP_ORDER=["id","idMal","title","description","coverImage","bannerImage","format","season","seasonYear","episodes","duration","status","averageScore","meanScore","popularity","favourites","trending","genres","tags","source","countryOfOrigin","isAdult","hashtag","synonyms","siteUrl","trailer","studios","mainStudios","updatedAt","nextAiringEpisode","startDate","endDate","characters","staff","relations","recommendations","externalLinks","streamingEpisodes","stats","_extrasAt"]
_MEDIA_REF=["id","title","coverImage","bannerImage","isAdult","format","episodes","status","meanScore","averageScore","popularity","genres","startDate"]
ORDER_BY_PATH={"":TOP_ORDER,"studios":["nodes"],"mainStudios":["nodes"],"studios.nodes":["id","name","isAnimationStudio","siteUrl"],"mainStudios.nodes":["name","isAnimationStudio"],
 "tags":["name","rank","isMediaSpoiler"],"relations":["edges"],"relations.edges":["relationType","node"],
 "relations.edges.node":["id","title","coverImage","bannerImage","isAdult","format","type","status","episodes","chapters","meanScore","averageScore","popularity","startDate"],
 "recommendations":["pageInfo","nodes"],"recommendations.nodes":["rating","mediaRecommendation"],"recommendations.nodes.mediaRecommendation":_MEDIA_REF,
 "characters":["pageInfo","edges"],"characters.edges":["role","node","voiceActors"],"characters.edges.node":["id","name","image"],"characters.edges.voiceActors":["id","name","image","languageV2"],
 "staff":["edges"],"staff.edges":["role","node"],"staff.edges.node":["id","name","image"],
 "externalLinks":["url","site","type"],"streamingEpisodes":["title","thumbnail","url","site"],
 "stats":["scoreDistribution","statusDistribution"],"stats.scoreDistribution":["score","amount"],"stats.statusDistribution":["status","amount"]}
ORDER_BY_KEY={"title":["romaji","english","native"],"coverImage":["large","extraLarge","medium","color"],"trailer":["id","site","thumbnail"],"nextAiringEpisode":["episode","airingAt","timeUntilAiring"],
 "startDate":["year","month","day"],"endDate":["year","month","day"],"pageInfo":["total","currentPage","hasNextPage","perPage"],"name":["full","native","userPreferred"],"image":["large","medium"]}
def ordered(value,path=""):
    """Return value with every object's keys in the fixed order. Lists keep their order; keys not listed keep their incoming order after the listed ones."""
    if isinstance(value,list):return [ordered(v,path) for v in value]
    if not isinstance(value,dict):return value
    spec=ORDER_BY_PATH.get(path) or ORDER_BY_KEY.get(path.rsplit(".",1)[-1]) or []
    rank={k:i for i,k in enumerate(spec)}
    keys=sorted(value,key=lambda k:rank.get(k,len(rank)))  # stable: unlisted keys stay in their incoming order
    return {k:ordered(value[k],f"{path}.{k}" if path else k) for k in keys}
LIST_KEYS=("id","title","bannerImage","format","season","seasonYear","episodes","duration","status","averageScore","meanScore","popularity","favourites","genres","tags","source","countryOfOrigin","isAdult","nextAiringEpisode","startDate","endDate")
def fresh_airing(x,now=None):
    """AniList's timeUntilAiring is relative to the moment of the fetch, so a stored value goes stale. Recompute it from airingAt on every response."""
    a=x.get("nextAiringEpisode")
    if isinstance(a,dict) and a.get("airingAt") is not None:x["nextAiringEpisode"]={**a,"timeUntilAiring":max(0,a["airingAt"]-(now or int(time.time())))}
    return x
def slim(x):
    """The slim list shape (same fields as Miruro's list routes): no description, characters, relations or recommendations, and main studios only."""
    cover=x.get("coverImage")or{}
    main=x.get("mainStudios")
    if main is None:  # record not refreshed since main studios were stored: animation studios are the closest match
        main={"nodes":[{"name":m.get("name"),"isAnimationStudio":m.get("isAnimationStudio")} for m in (x.get("studios")or{}).get("nodes")or[] if m.get("isAnimationStudio")]}
    out={k:x[k] for k in LIST_KEYS if k in x}
    out["coverImage"]={k:cover[k] for k in (("large","extraLarge") if STRICT else ("large","extraLarge","color")) if k in cover}
    out["studios"]=main
    return ordered(fresh_airing(out))
# Strict Miruro shape (API_STRICT_MIRURO_SHAPE, default on): every route returns exactly the fields Miruro's live-AniList route returns.
# Turn it off to keep the richer stored fields (cover colors, isAdult and more on related titles, updatedAt, ...).
STRICT=os.getenv("API_STRICT_MIRURO_SHAPE","true").strip().lower() in {"1","true","yes","on"}
def _pick(d,keys):return {k:d[k] for k in keys if k in d}
def _node(n,keys,cover):
    if not isinstance(n,dict):return n
    out=_pick(n,keys)
    if isinstance(out.get("coverImage"),dict):out["coverImage"]=_pick(out["coverImage"],cover)
    return out
RELATION_INFO_KEYS=("id","title","coverImage","format","type","status","episodes","meanScore")
RELATION_ROUTE_KEYS=("id","title","coverImage","bannerImage","format","type","status","episodes","chapters","meanScore","averageScore","popularity","startDate")
REC_INFO_KEYS=("id","title","coverImage","format","episodes","status","meanScore","averageScore")
REC_ROUTE_KEYS=("id","title","coverImage","bannerImage","format","episodes","status","meanScore","averageScore","popularity","genres","startDate")
def strict_edges(edges,keys,cover):
    return [{**e,"node":_node(e.get("node"),keys,cover)} for e in edges or []] if STRICT else edges
def strict_recs(nodes,keys,cover):
    return [{**r,"mediaRecommendation":_node(r.get("mediaRecommendation"),keys,cover)} for r in nodes or []] if STRICT else nodes
def strict_info(m):
    """Trim a stored record to the exact field set of Miruro's /info."""
    if not STRICT:return m
    m.pop("updatedAt",None)
    for e in (m.get("characters")or{}).get("edges")or[]:
        n=e.get("node")
        if isinstance(n,dict):
            if isinstance(n.get("name"),dict):n["name"]=_pick(n["name"],("full","native"))
            if isinstance(n.get("image"),dict):n["image"]=_pick(n["image"],("large",))
    if isinstance(m.get("relations"),dict):m["relations"]["edges"]=strict_edges(m["relations"].get("edges"),RELATION_INFO_KEYS,("large",))
    if isinstance(m.get("recommendations"),dict):m["recommendations"]["nodes"]=strict_recs(m["recommendations"].get("nodes"),REC_INFO_KEYS,("large",))
    return m
def clean(x):
    """Drop bookkeeping keys that are stored with a record but are not part of the public shape."""
    x.pop("mainStudios",None); x.pop("_extrasAt",None); return x

def fill_colors(items):
    """Add coverImage.color to related and recommended titles stored before it was requested, using the color already held for those titles in the catalog."""
    nodes={}
    for x in items:
        for e in (x.get("relations")or{}).get("edges")or[]:
            n=e.get("node")or{}
            if n.get("id") and isinstance(n.get("coverImage"),dict) and "color" not in n["coverImage"]:nodes.setdefault(n["id"],[]).append(n["coverImage"])
        for r in (x.get("recommendations")or{}).get("nodes")or[]:
            n=r.get("mediaRecommendation")or{}
            if n.get("id") and isinstance(n.get("coverImage"),dict) and "color" not in n["coverImage"]:nodes.setdefault(n["id"],[]).append(n["coverImage"])
    if not nodes:return
    with catalog.cursor() as c:
        c.execute("SELECT id, payload#>>'{coverImage,color}' FROM anilist_catalog WHERE id = ANY(%s)",(list(nodes),))
        for i,color in c.fetchall():
            for cover in nodes[i]:cover["color"]=color

def rows(where,params,order,page,n,adult=False,shape="full"):
    """shape: full (stored record), slim (list shape) or raw (stored record, adult links untouched)."""
    if not catalog.pool:raise HTTPException(503,"catalog database unavailable")
    if not adult:where=[*where,"is_adult = false"]
    clause=" WHERE "+" AND ".join(where) if where else ""
    with catalog.cursor() as c:
        c.execute("SELECT count(*) FROM anilist_catalog"+clause,params); total=c.fetchone()[0]
        c.execute(f"SELECT payload FROM anilist_catalog{clause} ORDER BY {order} LIMIT %s OFFSET %s",[*params,n,(page-1)*n]); result=[x[0] for x in c.fetchall()]
    if shape=="slim":result=[slim(x) for x in result]
    elif shape=="full":
        if not adult:hide_adult_links(result)
        fill_colors(result)
    return {"page":page,"perPage":n,"total":total,"hasNextPage":page*n<total,"results":result}
def like(text):return "%"+text.replace("\\","\\\\").replace("%","\\%").replace("_","\\_")+"%"
SORT_COLUMNS={"POPULARITY":"popularity","SCORE":"average_score","TRENDING":"trending","START_DATE":"start_key","FAVOURITES":"favourites","UPDATED_AT":"media_updated_at"}
SORT_NAMES=[f"{k}_{d}" for k in (*SORT_COLUMNS,"ID") for d in ("DESC","ASC")]
def sort_order(sort):
    """NAME_DESC is most first, NAME_ASC is least first. Missing values always sort last and ties fall back to popularity, then id, so pages never overlap."""
    m=re.fullmatch(r"([A-Z_]+)_(ASC|DESC)",(sort or "").strip().upper())
    if not m or (m[1]!="ID" and m[1] not in SORT_COLUMNS):raise HTTPException(400,f"Unknown sort '{sort}'. Use one of: {', '.join(SORT_NAMES)}.")
    name,direction=m[1],m[2]
    if name=="ID":return f"id {direction}"
    tie="" if name=="POPULARITY" else "popularity DESC NULLS LAST,"
    return f"{SORT_COLUMNS[name]} {direction} NULLS LAST,{tie}id ASC"
async def query(search=None,genre=None,tag=None,year=None,season=None,format=None,status=None,sort="POPULARITY_DESC",page=1,n=20,adult=False):
    where=[];p=[]
    if search and search.strip():
        # Every word must appear somewhere in the titles/synonyms, in any order, so
        # "shingeki kyojin" and "re zero" match like AniList's own search does.
        for word in re.findall(r"\w+",search) or [search.strip()]:where.append("search_text ILIKE %s");p.append(like(word))
    for col,val in (("season_year =",year),("season =",season.upper() if season else None),("format =",format.upper() if format else None),("status =",status.upper() if status else None)):
        if val is not None:where.append(col+" %s");p.append(val)
    if genre:where.append("genres @> %s");p.append(genre if isinstance(genre,list) else [genre])
    if tag:where.append("tags @> %s");p.append(tag if isinstance(tag,list) else [tag])
    order=sort_order(sort)
    return await asyncio.to_thread(rows,where,p,order,page,n,adult,"slim")

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def landing():
    """Landing page and endpoint reference (ui/landing.html, designed by AziF)."""
    html = await asyncio.to_thread((UI_DIR / "landing.html").read_text, encoding="utf-8")
    # Version the asset URLs by file mtime so browsers never keep a stale landing.js/css.
    for name in ("landing.js", "landing.css", "endpoints.js"):
        try: stamp = int((UI_DIR / "assets" / name).stat().st_mtime)
        except OSError: continue
        html = html.replace(f"/assets/{name}", f"/assets/{name}?v={stamp}")
    return HTMLResponse(html.replace("{{VERSION}}", APP_VERSION), headers={"Cache-Control": "no-cache"})

@app.get("/favicon.ico", include_in_schema=False)
@app.get("/logo.png", include_in_schema=False)
async def favicon():
    """Browsers request this for bare JSON routes, so API tabs share the landing-page icon."""
    return FileResponse(UI_DIR / "assets" / "favicon-64.png", media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})

@app.get("/swagger", include_in_schema=False)
async def swagger_docs():
    return get_swagger_ui_html(openapi_url=app.openapi_url, title="AniServe - Swagger", swagger_favicon_url="/favicon.ico")

@app.get("/license", response_class=PlainTextResponse, include_in_schema=False)
async def license_text():
    if not LICENSE_FILE.is_file(): raise HTTPException(404, "LICENSE file not found")
    return PlainTextResponse(await asyncio.to_thread(LICENSE_FILE.read_text, encoding="utf-8"))

HEALTH_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="health")

def health_debug_sync():
    debug = {"database_connected": bool(catalog.pool), "last_error": catalog.last_error,
             "config": {"pool_size": catalog.pool_size, "strict_miruro_shape": STRICT, "api_rpm": api_limiter.rpm, "api_window_seconds": api_limiter.window}}
    if catalog.pool:
        with catalog.cursor() as c:
            c.execute("SELECT count(*), max(indexed_at) FROM anilist_catalog")
            count, last_indexed = c.fetchone()
            debug["catalog"] = {"entries": count, "last_indexed_at": last_indexed}
            try:
                c.execute("SELECT key, updated_at FROM anilist_index_state ORDER BY key")
                debug["catalog"]["jobs"] = {key: updated for key, updated in c.fetchall()}
            except psycopg2.errors.UndefinedTable: debug["catalog"]["jobs"] = None
    return debug

@app.get("/health")
async def health():
    result = {"status": "online", "environment": ENVIRONMENT, "database": "connected" if catalog.pool else "unavailable"}
    if IS_DEVELOPMENT:
        try: result["debug"] = await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(HEALTH_EXECUTOR, health_debug_sync), timeout=3)
        except asyncio.TimeoutError:
            result["debug"] = {"diagnostic_error": "diagnostics timed out (database busy)"}
        except Exception as exc:
            LOG.exception("Development health diagnostic failed")
            result["debug"] = {"diagnostic_error": str(exc)}
    return result
@app.get("/list")
async def list_all(page:int=Query(1,ge=1),per_page:int=Query(100,ge=1,le=500),ids_only:bool=False,is_adult:Adult=False):
    """Every indexed title with its full stored record, ordered by id. Adult titles only when isAdult=true."""
    if not catalog.pool:raise HTTPException(503,"catalog database unavailable")
    def get():
        clause="" if is_adult else " WHERE is_adult = false"
        with catalog.cursor() as c:
            c.execute("SELECT count(*) FROM anilist_catalog"+clause); total=c.fetchone()[0]
            c.execute("SELECT id"+("" if ids_only else ", payload")+" FROM anilist_catalog"+clause+" ORDER BY id LIMIT %s OFFSET %s",[per_page,(page-1)*per_page])
            result=[r[0] for r in c.fetchall()] if ids_only else [r[1] for r in c.fetchall()]
        if not is_adult and not ids_only:hide_adult_links(result)
        if not ids_only:
            fill_colors(result);result=[ordered(fresh_airing(clean(x))) for x in result]
        return {"page":page,"perPage":per_page,"total":total,"hasNextPage":page*per_page<total,"results":result}
    return await asyncio.to_thread(get)
PageQ=Annotated[int,Query(ge=1,description="Page number, starting at 1.")]
def PerPage(default,maximum=50):return Annotated[int,Query(ge=1,le=maximum,description=f"Results per page, 1 to {maximum}.")]
# Detail routes (characters / recommendations / relations): leave per_page out and the route returns everything it has in one response.
OptPerPage=Annotated[int|None,Query(ge=1,le=50,description="Results per page, 1 to 50. Omit it to get every item in one response.")]
@app.get("/search")
async def search(query_:str=Query(...,alias="query"),page:PageQ=1,per_page:PerPage(20)=20,is_adult:Adult=False): return await query(search=query_,page=page,n=per_page,adult=is_adult)
@app.get("/multi-search")
async def multi_search(q:str|None=Query(None,min_length=1),genre:list[str]|None=Query(None),tag:list[str]|None=Query(None),year:int|None=None,season:str|None=None,format:str|None=None,status:str|None=None,sort:str="POPULARITY_DESC",page:PageQ=1,per_page:PerPage(30)=30,is_adult:Adult=False): return await query(q,genre,tag,year,season,format,status,sort,page,per_page,adult=is_adult)
@app.get("/filter")
async def filter(q:str|None=Query(None,min_length=1),genre:list[str]|None=Query(None),tag:list[str]|None=Query(None),year:int|None=None,season:str|None=None,format:str|None=None,status:str|None=None,sort:str="POPULARITY_DESC",page:PageQ=1,per_page:PerPage(20)=20,is_adult:Adult=False): return await query(q,genre,tag,year,season,format,status,sort,page,per_page,adult=is_adult)
@app.get("/suggestions")
async def suggestions(query_:str=Query(...,alias="query",min_length=1),is_adult:Adult=False):
    data=await query(search=query_,page=1,n=8,adult=is_adult);return {"suggestions":[{"id":x["id"],"title":(x.get("title")or{}).get("english")or(x.get("title")or{}).get("romaji"),"title_romaji":(x.get("title")or{}).get("romaji"),"poster":(x.get("coverImage")or{}).get("large"),**({} if STRICT else {"coverImage":x.get("coverImage")or{}}),"format":x.get("format"),"status":x.get("status"),"year":(x.get("startDate")or{}).get("year"),"episodes":x.get("episodes")} for x in data["results"]]}
@app.get("/trending")
async def trending(page:PageQ=1,per_page:PerPage(20)=20,is_adult:Adult=False):return await query(sort="TRENDING_DESC",page=page,n=per_page,adult=is_adult)
@app.get("/popular")
async def popular(page:PageQ=1,per_page:PerPage(20)=20,is_adult:Adult=False):return await query(page=page,n=per_page,adult=is_adult)
@app.get("/recent")
async def recent(page:PageQ=1,per_page:PerPage(20)=20,is_adult:Adult=False):return await query(status="RELEASING",sort="START_DATE_DESC",page=page,n=per_page,adult=is_adult)
@app.get("/upcoming")
async def upcoming(page:PageQ=1,per_page:PerPage(20)=20,is_adult:Adult=False):return await query(status="NOT_YET_RELEASED",page=page,n=per_page,adult=is_adult)
@app.get("/spotlight")
async def spotlight(is_adult:Adult=False):return {"results":(await query(sort="TRENDING_DESC",n=10,adult=is_adult))["results"]}
@app.get("/genres")
async def genres(is_adult:Adult=False):
    """The fixed frontend-supported genre list (same as Miruro). Hentai only with isAdult=true."""
    return {"results":[{"name":g,"isAdult":g=="Hentai"} for g in GENRES if is_adult or g!="Hentai"]}
@app.get("/tags")
async def tags(is_adult:Adult=False):
    """The fixed frontend-supported tag list (same as Miruro)."""
    return {"results":[{"name":t,"isAdult":False} for t in TAGS]}
def get_media(anilist_id,adult,shape="full"):
    data=rows(["id = %s"],[anilist_id],"id",1,1,adult,shape)
    if not data["results"]:raise HTTPException(404,"Anime not found")
    return data["results"][0]
def read_blocks(anilist_id,kind,pages,lowest,highest):
    """({page: items} for the indexed blocks among `pages`, how many of the blocks lowest..highest are indexed)."""
    try:
        with catalog.cursor() as c:
            blocks={}
            if pages:
                c.execute("SELECT page,items FROM anilist_detail_pages WHERE id=%s AND kind=%s AND page = ANY(%s)",(anilist_id,kind,list(pages)));blocks=dict(c.fetchall())
            c.execute("SELECT count(*) FROM anilist_detail_pages WHERE id=%s AND kind=%s AND page BETWEEN %s AND %s",(anilist_id,kind,lowest,highest));return blocks,c.fetchone()[0]
    except psycopg2.errors.UndefinedTable:
        return {},0  # a database without the optional anilist_detail_pages table: only the 25 items stored on the record are served
INDEX_HEADER="X-Index-Status"  # "complete", or "partial" while the background jobs have not indexed everything for this title yet
INFO_RECS=10  # /info lists 10 recommendations; the catalog now stores 25 per title, the rest is for /anime/{id}/recommendations
def finish_info(media,is_adult):
    """What get_media's "full" shape does, applied after the recommendations were cut to INFO_RECS (so adult ones are hidden from those 10, as before)."""
    rc=media.get("recommendations")
    if isinstance(rc,dict):rc["nodes"]=(rc.get("nodes")or[])[:INFO_RECS]
    if not is_adult:hide_adult_links([media])
    fill_colors([media])
    return media
@app.get("/info/{anilist_id}")
async def info(anilist_id:int,response:Response,is_adult:Adult=False):
    media=await asyncio.to_thread(get_media,anilist_id,is_adult,"raw")
    media=await asyncio.to_thread(finish_info,media,is_adult)
    # Staff, links, streaming episodes and stats are indexed by the deep-index extras job; a title it has not reached yet reports them empty.
    response.headers[INDEX_HEADER]="complete" if "_extrasAt" in media else "partial"
    for k in EXTRA_KEYS:
        if media.get(k) is None:media[k]=json.loads(json.dumps(EXTRA_EMPTY[k]))
    clean(media); fresh_airing(media)
    for key in ("characters","recommendations"):
        if isinstance(media.get(key),dict):media[key].pop("pageInfo",None)
    for e in (media.get("characters")or{}).get("edges")or[]:
        e["voiceActors"]=[v for v in e.get("voiceActors")or[] if v.get("languageV2")=="Japanese"]  # /info lists Japanese cast only
    return ordered(strict_info(media))

# ---- characters / recommendations beyond the first page ----
# Read-only: the API never calls AniList. Everything is kept in blocks of 25 (AniList's page size): block 1 is the first page stored in the
# catalog record by the crawl, every later block is stored in anilist_detail_pages by the deep-index pages job (Catalog.job_detail_pages).
# The client's per_page does not matter: a request is cut from whichever blocks it overlaps; without per_page the whole list is returned. What the job has not indexed yet is simply
# not served: the response carries the items indexed so far, hasNextPage only promises items that exist, `total` is AniList's real total,
# and the X-Index-Status header says "partial". An old block (past its refresh age) is served as it is until the job replaces it.
def detail_window(media,kind,lo,hi):
    """(items lo..hi (0-based, hi exclusive; None = up to the total) that are indexed, AniList's total, how many items are indexed from the
    start without a gap, whether the whole list is indexed)."""
    key=DETAIL_KEYS[kind]
    rec=media.get(kind)or{};stored=rec.get(key)or[];total=(rec.get("pageInfo")or{}).get("total")
    if total is None:total=len(stored)
    if hi is None:hi=total
    if total<=len(stored):return stored[lo:min(hi,total)],total,total,True  # the catalog record alone is complete
    blocks=-(-total//BLOCK); own=len(stored)>=min(BLOCK,total); lowest=2 if own else 1  # block 1 is the record's own page unless an old row stored fewer than 25
    first,last=(lo//BLOCK+1,min(hi,total-1)//BLOCK+1) if lo<total else (1,0)  # last also covers the item right after the window, to know whether a next page exists
    cached,indexed=read_blocks(media["id"],kind,range(first,last+1),lowest,blocks)
    out=[]
    for p in range(first,last+1):
        items=stored[:BLOCK] if p==1 and own else cached.get(p) or (stored[:BLOCK] if p==1 else [])  # an old row's own short page 1 beats nothing
        out+=items
        if len(items)<min(BLOCK,total-(p-1)*BLOCK):break  # a missing or short block: nothing after it can be served in order
    start=(first-1)*BLOCK
    return out[lo-start:min(hi,total)-start],total,start+len(out),indexed>=blocks-lowest+1
def detail_bounds(page,per_page):
    """(lo, hi) item offsets for a request. Without per_page the whole list is page 1, so any later page is empty."""
    if per_page:return (page-1)*per_page,page*per_page
    return (0 if page==1 else 10**9),None
def detail_response(response,page,per_page,total,have,complete,key,items):
    response.headers[INDEX_HEADER]="complete" if complete else "partial"
    if not per_page:return {"page":page,"perPage":total,"total":total,"hasNextPage":False,key:items}  # no per_page: everything is on page 1
    return {"page":page,"perPage":per_page,"total":total,"hasNextPage":page*per_page<min(have,total),key:items}
@app.get("/anime/{anilist_id}/characters")
async def chars(anilist_id:int,response:Response,page:PageQ=1,per_page:OptPerPage=None,is_adult:Adult=False):
    media=await asyncio.to_thread(get_media,anilist_id,is_adult)
    edges,total,have,complete=await asyncio.to_thread(detail_window,media,"characters",*detail_bounds(page,per_page))
    return detail_response(response,page,per_page,total,have,complete,"characters",ordered(edges,"characters.edges"))
@app.get("/anime/{anilist_id}/relations")
async def relations(anilist_id:int,page:PageQ=1,per_page:OptPerPage=None,is_adult:Adult=False):
    media=await asyncio.to_thread(get_media,anilist_id,is_adult);title=_pick(media["title"],("romaji","english")) if STRICT else media["title"]
    edges=ordered(strict_edges((media.get("relations")or{}).get("edges",[]),RELATION_ROUTE_KEYS,("large",)),"relations.edges")
    out={"id":media["id"],"title":ordered(title,"title"),"relations":edges}
    if per_page:  # paging fields only when asked for, so the default body stays exactly Miruro's
        lo,hi=detail_bounds(page,per_page);out.update({"page":page,"perPage":per_page,"total":len(edges),"hasNextPage":hi<len(edges),"relations":edges[lo:hi]})
    return out
def finish_recs(nodes,is_adult):
    """Hide adult recommendations (unless isAdult=true) and fill cover colors. Runs after paging so offsets match AniList's."""
    holder={"recommendations":{"nodes":nodes}}
    if not is_adult:hide_adult_links([holder])
    fill_colors([holder]);return ordered(strict_recs(holder["recommendations"]["nodes"],REC_ROUTE_KEYS,("large","extraLarge")),"recommendations.nodes")
@app.get("/anime/{anilist_id}/recommendations")
async def recommendations(anilist_id:int,response:Response,page:PageQ=1,per_page:OptPerPage=None,is_adult:Adult=False):
    media=await asyncio.to_thread(get_media,anilist_id,is_adult,"raw")  # unfiltered, so the stored page can be sliced like AniList's
    nodes,total,have,complete=await asyncio.to_thread(detail_window,media,"recommendations",*detail_bounds(page,per_page))
    return detail_response(response,page,per_page,total,have,complete,"recommendations",await asyncio.to_thread(finish_recs,nodes,is_adult))
def schedule_item(x,now):
    """Miruro-compatible schedule entry: slim list fields plus next_episode / airingAt / timeUntilAiring (and flat color / banner / synopsis when API_STRICT_MIRURO_SHAPE is off)."""
    a=x.get("nextAiringEpisode")or{};airing=a.get("airingAt")
    item=slim(x)
    if not STRICT:item.update({"color":(x.get("coverImage")or{}).get("color"),"banner":x.get("bannerImage"),"synopsis":x.get("description")})
    item.update({"next_episode":a.get("episode"),"airingAt":airing,"timeUntilAiring":max(0,(airing or now)-now)})
    return item
@app.get("/schedule")
async def schedule(page:PageQ=1,per_page:PerPage(20)=20,is_adult:Adult=False):
    """Upcoming airings, soonest first: one entry per title for its next episode."""
    data=await asyncio.to_thread(rows,["next_airing_at > now()"],[],"next_airing_at ASC,id",page,per_page,is_adult,"raw")
    now=int(time.time())
    data["results"]=[schedule_item(x,now) for x in data["results"]]
    return data
