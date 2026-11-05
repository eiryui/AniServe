"""AniServe: a read-only JSON API over an AniList database dump.

Point DATABASE_URL at PostgreSQL, SQLite, DuckDB or Parquet files loaded from the dump
(https://github.com/eiryui/AniList-DB-Dump/releases/latest) and AniServe serves the catalogue as JSON.
It never writes to the data and never contacts AniList.

Derived from code by JsmaAtom (MIT). See LICENSE.
"""
import asyncio, json, logging, math, os, re, threading, time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()
from shape import (STRICT, REC_ROUTE_KEYS, RELATION_ROUTE_KEYS, _pick, apply_colours, clean, colour_targets, fresh_airing, hide_adult,  # noqa: E402
                   linked_ids, ordered, slim, strict_edges, strict_info, strict_recs)
from stores import Catalog, StoreUnavailable, SORT_COLUMNS  # noqa: E402

APP_NAME = "AniServe"
APP_VERSION = "1.0.0"
LOG = logging.getLogger("aniserve")
LOG.setLevel(logging.INFO)
BASE_DIR = Path(__file__).resolve().parent
UI_DIR = BASE_DIR / "ui"
ENABLE_EXPORT = os.getenv("ANISERVE_ENABLE_EXPORT", "true").strip().lower() in {"1", "true", "yes", "on"}

catalog = Catalog(os.getenv("DATABASE_URL", ""))


def store():
    return catalog.need()


def load_lists():
    """Genre and tag lists shared with the Eiryui frontend, read from genres-tags.js."""
    src = (BASE_DIR / "genres-tags.js").read_text(encoding="utf-8")
    def grab(name):
        start = src.index(f"const {name} = ") + len(f"const {name} = ")
        return json.loads(src[start:src.index(";", start)])
    return grab("GENRES"), grab("TAGS")


GENRES, TAGS = load_lists()


# --------------------------------------------------------------------------- #
# Queries
# --------------------------------------------------------------------------- #
def hide_adult_links(items):
    """Drop adult titles from the relations and recommendations inside records."""
    ids = linked_ids(items)
    if ids: hide_adult(items, store().adult_ids(ids))


def fill_colors(items):
    """Add coverImage.color to related and recommended titles stored before it was requested, from the colour the catalogue holds for them."""
    targets = colour_targets(items)
    if targets: apply_colours(targets, store().colours(targets))


def rows(f, order, page, n, adult=False, shape="full"):
    """shape: full (record), slim (list shape) or raw (record, adult links untouched)."""
    total, result = store().page(f, order, page, n, adult)
    if shape == "slim":
        result = [slim(x) for x in result]
    elif shape == "full":
        if not adult: hide_adult_links(result)
        fill_colors(result)
    return {"page": page, "perPage": n, "total": total, "hasNextPage": page * n < total, "results": result}


SORT_NAMES = [f"{k}_{d}" for k in (*SORT_COLUMNS, "ID") for d in ("DESC", "ASC")]


def sort_order(sort):
    """NAME_DESC is most first, NAME_ASC is least first. Missing values sort last; ties fall back to popularity, then id."""
    m = re.fullmatch(r"([A-Z_]+)_(ASC|DESC)", (sort or "").strip().upper())
    if not m or (m[1] != "ID" and m[1] not in SORT_COLUMNS):
        raise HTTPException(400, f"Unknown sort '{sort}'. Use one of: {', '.join(SORT_NAMES)}.")
    return ("id" if m[1] == "ID" else m[1]), m[2]


async def query(search=None, genre=None, tag=None, year=None, season=None, format=None, status=None, sort="POPULARITY_DESC", page=1, n=20, adult=False):
    f = {"year": year, "season": season.upper() if season else None, "format": format.upper() if format else None, "status": status.upper() if status else None,
         "genres": genre if isinstance(genre, list) else ([genre] if genre else []), "tags": tag if isinstance(tag, list) else ([tag] if tag else [])}
    if search and search.strip():
        f["words"] = re.findall(r"\w+", search) or [search.strip()]  # every word must appear in a title or synonym, in any order
    return await asyncio.to_thread(rows, f, sort_order(sort), page, n, adult, "slim")


# --------------------------------------------------------------------------- #
# App, rate limiter
# --------------------------------------------------------------------------- #
class ApiRateLimiter:
    """Small in-memory fixed-window limiter per client IP."""
    def __init__(self):
        self.rpm = int(os.getenv("API_RATE_LIMIT_RPM", "120"))
        self.window = max(1, int(os.getenv("API_RATE_LIMIT_WINDOW_SECONDS", "60")))
        self.limit = 0 if self.rpm <= 0 else max(1, math.ceil(self.rpm * self.window / 60))
        self.trust_proxy = os.getenv("API_RATE_LIMIT_TRUST_PROXY", "false").strip().lower() in {"1", "true", "yes", "on"}
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


@asynccontextmanager
async def lifespan(_app):
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=int(os.getenv("THREAD_POOL_SIZE", "32")), thread_name_prefix="aniserve"))
    await asyncio.to_thread(catalog.connect)
    if catalog.problem: LOG.error("AniServe started without a usable catalogue: %s", catalog.problem)
    else: LOG.info("Serving %s", catalog.engine)
    yield
    if catalog.store: catalog.store.close()


app = FastAPI(title=APP_NAME, version=APP_VERSION, description="Read-only JSON API over an AniList database dump.", license_info={"name": "MIT", "identifier": "MIT"}, lifespan=lifespan)
app.mount("/assets", StaticFiles(directory=UI_DIR / "assets"), name="assets")
origins = [x.strip() for x in os.getenv("ALLOWED_ORIGINS", "*").split(",")]
# CORS is wide open by default and the API is read-only and cookie-less, so credentials stay off.
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False, allow_methods=["GET", "OPTIONS"], allow_headers=["*"], expose_headers=["X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset", "X-AniServe-Ms"])


@app.exception_handler(StoreUnavailable)
async def unavailable(_request, exc):
    return JSONResponse(status_code=503, content={"detail": str(exc) or "catalogue unavailable"})


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
    response.headers["X-AniServe-Ms"] = f"{(time.perf_counter() - started) * 1000:.1f}"
    return response


Adult = Annotated[bool, Query(alias="isAdult", description="true includes adult titles alongside the rest; false or omitted hides them.")]
PageQ = Annotated[int, Query(ge=1, description="Page number, starting at 1.")]


def PerPage(default, maximum=50):
    return Annotated[int, Query(ge=1, le=maximum, description=f"Results per page, 1 to {maximum}.")]


# --------------------------------------------------------------------------- #
# Site routes
# --------------------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def home():
    html = await asyncio.to_thread((UI_DIR / "index.html").read_text, encoding="utf-8")
    for name in ("aniserve.js", "aniserve.css"):  # version asset URLs by mtime so browsers never keep a stale copy
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
    return get_swagger_ui_html(openapi_url=app.openapi_url, title="AniServe - Swagger", swagger_favicon_url="/favicon.svg")


@app.get("/license", response_class=PlainTextResponse, include_in_schema=False)
async def license_text():
    return PlainTextResponse(await asyncio.to_thread((BASE_DIR / "LICENSE").read_text, encoding="utf-8"))


@app.get("/health")
async def health():
    await asyncio.to_thread(catalog.ensure)
    out = {"status": "online" if catalog.ok else "degraded", "name": APP_NAME, "version": APP_VERSION, "database": "connected" if catalog.ok else "unavailable", "mode": "read-only"}
    try: out["engine"] = catalog.engine
    except StoreUnavailable: pass
    if catalog.problem: out["problem"] = catalog.problem
    return out


# --------------------------------------------------------------------------- #
# API routes: lists and search
# --------------------------------------------------------------------------- #
@app.get("/search")
async def search(query_: str = Query(..., alias="query"), page: PageQ = 1, per_page: PerPage(20) = 20, is_adult: Adult = False):
    return await query(search=query_, page=page, n=per_page, adult=is_adult)


@app.get("/multi-search")
async def multi_search(q: str | None = Query(None, min_length=1), genre: list[str] | None = Query(None), tag: list[str] | None = Query(None), year: int | None = None, season: str | None = None, format: str | None = None, status: str | None = None, sort: str = "POPULARITY_DESC", page: PageQ = 1, per_page: PerPage(30) = 30, is_adult: Adult = False):
    return await query(q, genre, tag, year, season, format, status, sort, page, per_page, adult=is_adult)


@app.get("/filter")
async def filter(q: str | None = Query(None, min_length=1), genre: list[str] | None = Query(None), tag: list[str] | None = Query(None), year: int | None = None, season: str | None = None, format: str | None = None, status: str | None = None, sort: str = "POPULARITY_DESC", page: PageQ = 1, per_page: PerPage(20) = 20, is_adult: Adult = False):
    return await query(q, genre, tag, year, season, format, status, sort, page, per_page, adult=is_adult)


@app.get("/suggestions")
async def suggestions(query_: str = Query(..., alias="query", min_length=1), is_adult: Adult = False):
    data = await query(search=query_, page=1, n=8, adult=is_adult)
    out = []
    for x in data["results"]:
        t = x.get("title") or {}
        out.append({"id": x["id"], "title": t.get("english") or t.get("romaji"), "title_romaji": t.get("romaji"), "poster": (x.get("coverImage") or {}).get("large"),
                    **({} if STRICT else {"coverImage": x.get("coverImage") or {}}), "format": x.get("format"), "status": x.get("status"), "year": (x.get("startDate") or {}).get("year"), "episodes": x.get("episodes")})
    return {"suggestions": out}


@app.get("/trending")
async def trending(page: PageQ = 1, per_page: PerPage(20) = 20, is_adult: Adult = False):
    return await query(sort="TRENDING_DESC", page=page, n=per_page, adult=is_adult)


@app.get("/popular")
async def popular(page: PageQ = 1, per_page: PerPage(20) = 20, is_adult: Adult = False):
    return await query(page=page, n=per_page, adult=is_adult)


@app.get("/recent")
async def recent(page: PageQ = 1, per_page: PerPage(20) = 20, is_adult: Adult = False):
    return await query(status="RELEASING", sort="START_DATE_DESC", page=page, n=per_page, adult=is_adult)


@app.get("/upcoming")
async def upcoming(page: PageQ = 1, per_page: PerPage(20) = 20, is_adult: Adult = False):
    return await query(status="NOT_YET_RELEASED", page=page, n=per_page, adult=is_adult)


@app.get("/spotlight")
async def spotlight(is_adult: Adult = False):
    return {"results": (await query(sort="TRENDING_DESC", n=10, adult=is_adult))["results"]}


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
    data = await asyncio.to_thread(rows, {"airing": True}, ("next_airing_at", "ASC"), page, per_page, is_adult, "raw")
    now = int(time.time())
    data["results"] = [schedule_item(x, now) for x in data["results"]]
    return data


@app.get("/list")
async def list_all(page: int = Query(1, ge=1), per_page: int = Query(100, ge=1, le=500), ids_only: bool = False, is_adult: Adult = False):
    """Every title with its full record, ordered by id. Adult titles only when isAdult=true."""
    def get():
        total, result = store().page({}, ("id", "ASC"), page, per_page, is_adult, ids_only=ids_only)
        if not ids_only:
            if not is_adult: hide_adult_links(result)
            fill_colors(result); result = [ordered(fresh_airing(clean(x))) for x in result]
        return {"page": page, "perPage": per_page, "total": total, "hasNextPage": page * per_page < total, "results": result}
    return await asyncio.to_thread(get)


# --------------------------------------------------------------------------- #
# API routes: detail pages (stored data only, AniList is never contacted)
# --------------------------------------------------------------------------- #
def get_media(anilist_id, adult, shape="full"):
    data = rows({"ids": [anilist_id]}, ("id", "ASC"), 1, 1, adult, shape)
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
    """The full stored record. staff, externalLinks, streamingEpisodes and stats appear only when the dump holds them."""
    media = await asyncio.to_thread(lambda: finish_info(get_media(anilist_id, is_adult, "raw"), is_adult))
    clean(media); fresh_airing(media)
    for key in ("characters", "recommendations"):
        if isinstance(media.get(key), dict): media[key].pop("pageInfo", None)
    for e in (media.get("characters") or {}).get("edges") or []:
        e["voiceActors"] = [v for v in e.get("voiceActors") or [] if v.get("languageV2") == "Japanese"]  # /info lists Japanese cast only
    return ordered(strict_info(media))


BLOCK = 25  # characters / recommendations are stored in blocks of 25 (block 1 lives in the catalogue record)


def detail_window(media, kind, key, lo, hi):
    """Items lo..hi (0-based, hi exclusive) from the record plus any stored extra blocks. Stops at the first block that was never stored."""
    rec = media.get(kind) or {}; stored = rec.get(key) or []
    total = (rec.get("pageInfo") or {}).get("total")
    if total is None: total = len(stored)
    hi = min(hi, total)
    if lo >= hi: return [], total
    if hi <= len(stored): return stored[lo:hi], total
    first, last = lo // BLOCK + 1, (hi - 1) // BLOCK + 1
    have = store().blocks(media["id"], kind, max(first, 2), last) if last >= 2 else {}
    out = []
    for p in range(first, last + 1):
        items = stored[:BLOCK] if p == 1 and len(stored) >= min(BLOCK, total) else have.get(p)
        if items is None: break  # a gap: never stitch around it
        out += items
    return out[lo - (first - 1) * BLOCK:hi - (first - 1) * BLOCK], total


@app.get("/anime/{anilist_id}/characters")
async def chars(anilist_id: int, page: PageQ = 1, per_page: PerPage(25) = 25, is_adult: Adult = False):
    def get():
        media = get_media(anilist_id, is_adult)
        lo, hi = (page - 1) * per_page, page * per_page
        edges, total = detail_window(media, "characters", "edges", lo, hi)
        return {"page": page, "perPage": per_page, "total": total, "hasNextPage": hi < total, "characters": ordered(edges, "characters.edges")}
    return await asyncio.to_thread(get)


@app.get("/anime/{anilist_id}/relations")
async def relations(anilist_id: int, is_adult: Adult = False):
    media = await asyncio.to_thread(get_media, anilist_id, is_adult)
    title = _pick(media["title"], ("romaji", "english")) if STRICT else media["title"]
    return {"id": media["id"], "title": ordered(title, "title"), "relations": ordered(strict_edges((media.get("relations") or {}).get("edges", []), RELATION_ROUTE_KEYS, ("large",)), "relations.edges")}


@app.get("/anime/{anilist_id}/recommendations")
async def recommendations(anilist_id: int, page: PageQ = 1, per_page: PerPage(10, 25) = 10, is_adult: Adult = False):
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
# AniServe extras: /stats and /export
# --------------------------------------------------------------------------- #
_stats_cache = {}


@app.get("/stats")
async def stats(is_adult: Adult = False):
    """Counts by status, format, decade and genre, plus when each job in the dump last ran. Cached for 30 s."""
    hit = _stats_cache.get(is_adult)
    if hit and time.monotonic() - hit[0] < 30:
        return hit[1]
    data = await asyncio.to_thread(lambda: store().stats(is_adult))
    _stats_cache[is_adult] = (time.monotonic(), data)
    return data


_export_running = threading.Event()  # one full export at a time keeps the data layer free for the API


def export_lines(adult, fmt, batch=300):
    """Yield the whole catalogue as NDJSON (one record per line) or as one JSON array, in id order."""
    dumps = lambda x: json.dumps(x, ensure_ascii=False, separators=(",", ":"), default=str)
    _export_running.set()
    try:
        first, last = True, 0
        if fmt == "json": yield "["
        while True:
            items = store().after(last, batch, adult)
            if not items: break
            last = items[-1]["id"]
            if not adult: hide_adult_links(items)
            fill_colors(items)
            for x in items:
                rec = dumps(ordered(fresh_airing(clean(x))))
                if fmt == "json":
                    yield ("" if first else ",") + rec; first = False
                else:
                    yield rec + "\n"
        if fmt == "json": yield "]"
    finally:
        _export_running.clear()


@app.get("/export")
async def export(format: str = Query("ndjson", pattern="^(ndjson|json)$"), is_adult: Adult = False):
    """Stream the entire catalogue, full records ordered by id. ndjson (default) is one JSON object per line; json is a single array."""
    if not ENABLE_EXPORT: raise HTTPException(404, "Export is disabled (ANISERVE_ENABLE_EXPORT=false)")
    await asyncio.to_thread(store)  # fail with 503 before the response starts
    if _export_running.is_set(): raise HTTPException(429, "Another export is already running; try again shortly")
    media_type = "application/x-ndjson" if format == "ndjson" else "application/json"
    return StreamingResponse(export_lines(is_adult, format), media_type=media_type, headers={"Content-Disposition": f'attachment; filename="anilist-catalog.{"ndjson" if format == "ndjson" else "json"}"'})
