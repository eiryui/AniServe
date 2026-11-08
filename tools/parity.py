"""Parity check: run the original AniList Indexer API and AniServe against the SAME PostgreSQL and compare every response byte for byte.

  DATABASE_URL=postgresql://... python tools/parity.py /path/to/AniList-Indexer [--sample 150]

It imports the original app.py WITHOUT starting its indexer (no AniList calls, nothing written) and sends the same requests to both apps
through in-process test clients: every route, every sort, filters, paging edges, adult on/off and 4xx errors, for a sample of titles.
Status code, body bytes (so field ORDER too), Content-Type and X-Index-Status must match. Needs the original project's dependencies
(httpx, brotli-asgi, ...) plus httpx for the test client. Run it a second time with API_STRICT_MIRURO_SHAPE=false to cover the other shape.
Exit code 0 = identical. /health and /openapi.json are compared on the parts that exist in both (AniServe has no indexer fields).
"""
import argparse, importlib.util, itertools, json, os, sys, time
from pathlib import Path
ap = argparse.ArgumentParser(); ap.add_argument("original", help="folder holding the original app.py"); ap.add_argument("--sample", type=int, default=150, help="how many titles get the per-title routes (spread evenly over the ids; 0 = all)")
args = ap.parse_args()
ORIG = args.original; NEW = str(Path(__file__).resolve().parent.parent); DSN = os.environ["DATABASE_URL"]
os.environ["API_RATE_LIMIT_RPM"] = "0"
FIXED = time.time()
time.time = lambda: FIXED  # timeUntilAiring is computed from the clock: freeze it so two calls cannot straddle a second

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, os.path.join(path, "app.py")); m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m; spec.loader.exec_module(m); return m

import psycopg2, psycopg2.pool
from fastapi.testclient import TestClient
orig = load("orig_app", ORIG); new = load("new_app", NEW)
orig.catalog.pool = psycopg2.pool.ThreadedConnectionPool(1, 8, DSN, **orig.KEEPALIVE)  # no startup: the original's indexer must not run
oc = TestClient(orig.app); H = {"Accept-Encoding": "identity"}
conn = psycopg2.connect(DSN); conn.set_session(readonly=True); cur = conn.cursor(); cur.execute("select id from anilist_catalog order by id"); IDS = [r[0] for r in cur.fetchall()]
cur.execute("select id from anilist_catalog where is_adult order by id"); ADULT_ALL = [r[0] for r in cur.fetchall()]; conn.close()
if args.sample and len(IDS) > args.sample: IDS = IDS[::max(1, len(IDS) // args.sample)]
ADULT = [i for i in ADULT_ALL if i in set(IDS)] or ADULT_ALL[:5]

reqs = []
def add(path, **params): reqs.append((path, params))
A = [None, "true", "false"]
def P(**kw): return {k: v for k, v in kw.items() if v is not None}
words = ["shingeki", "Kyojin 12", "the sword", "100%", "_", "\\", "Alt Name", "日本語", "Pure_Wild", "zzzznomatch", "e", "re zero", "  ", "Studio"]
for q, pg, pp, a in itertools.product(words, [None, 1, 2], [None, 5, 50], A): add("/search", **P(query=q, page=pg, per_page=pp, isAdult=a))
add("/search"); add("/search", query=""); add("/search", query="a", per_page=0); add("/search", query="a", per_page=51); add("/search", query="a", page=0); add("/search", query="a", page="x")
for q, a in itertools.product(words + [""], A): add("/suggestions", **P(query=q, isAdult=a))
add("/suggestions")
SORTS = [f"{k}_{d}" for k in ("POPULARITY", "SCORE", "TRENDING", "START_DATE", "FAVOURITES", "UPDATED_AT", "ID") for d in ("DESC", "ASC")] + ["popularity_desc", "BOGUS_DESC", "SCORE", "", "ID_UP", "NAME_ASC"]
for route in ("/filter", "/multi-search"):
    for s, a, pg in itertools.product(SORTS, A, [1, 2]): add(route, **P(sort=s, isAdult=a, page=pg, per_page=15))
    for g in (["Action"], ["Action", "Comedy"], ["Hentai"], ["Nope"], ["Drama", "Fantasy", "Romance"]):
        for a in A: add(route, genre=g, **P(isAdult=a))
    for t in (["Isekai"], ["School", "Idol"], ["Nope"]): add(route, tag=t)
    for y in (None, 1985, 2000, 2010, 2024, 2027, 1800): add(route, **P(year=y, per_page=50))
    for se, f, st in itertools.product([None, "SPRING", "fall", "bogus"], [None, "TV", "movie", "ONA"], [None, "FINISHED", "releasing", "NOT_YET_RELEASED", "HIATUS", "CANCELLED"]):
        add(route, **P(season=se, format=f, status=st, isAdult="true", per_page=10))
    add(route, q="sword", genre=["Action"], year=2010, season="SPRING", format="TV", status="FINISHED", sort="SCORE_DESC", isAdult="true")
    add(route, q=""); add(route, per_page=0); add(route, per_page=51); add(route, page=999); add(route, year="x")
for route in ("/trending", "/popular", "/recent", "/upcoming", "/schedule"):
    for pg, pp, a in itertools.product([None, 1, 2, 3, 99], [None, 1, 7, 20, 50], A): add(route, **P(page=pg, per_page=pp, isAdult=a))
    add(route, per_page=0); add(route, per_page=51); add(route, page=0)
for a in A: add("/spotlight", **P(isAdult=a)); add("/genres", **P(isAdult=a)); add("/tags", **P(isAdult=a))
for pg, pp, io, a in itertools.product([None, 1, 3], [None, 7, 500], [None, "true"], A): add("/list", **P(page=pg, per_page=pp, ids_only=io, isAdult=a))
add("/list", per_page=501); add("/list", per_page=0)
for i in sorted(set(IDS + ADULT[:5])) + [1, 2, 9_999_999]:
    for a in A if (i in ADULT or i < 10) else ["true", None]:
        add(f"/info/{i}", **P(isAdult=a)); add(f"/anime/{i}/relations", **P(isAdult=a)); add(f"/anime/{i}/characters", **P(isAdult=a)); add(f"/anime/{i}/recommendations", **P(isAdult=a))
    for pg, pp in [(1, 10), (2, 10), (1, 25), (2, 25), (1, 30), (2, 30), (3, 30), (1, 50), (2, 50), (4, 20), (1, 1), (5, 5)]:
        for kind in ("characters", "recommendations", "relations"): add(f"/anime/{i}/{kind}", page=pg, per_page=pp, isAdult="true")
    for kind in ("characters", "recommendations", "relations"): add(f"/anime/{i}/{kind}", page=2); add(f"/anime/{i}/{kind}", per_page=0); add(f"/anime/{i}/{kind}", per_page=51)
add("/info/abc"); add("/anime/abc/characters")
for i in ADULT[:8]:
    for kind in ("characters", "recommendations", "relations"): add(f"/anime/{i}/{kind}", isAdult="true", per_page=25)
add("/license"); add("/favicon.ico"); add("/nope")

def norm_health(b):
    d = json.loads(b); [d.pop(k, None) for k in ("indexer", "indexer_state")]; return d
mismatch = 0; checked = 0; statuses = {}
with TestClient(new.app) as nc:
    for path, params in reqs:
        r1 = oc.get(path, params=params, headers=H); r2 = nc.get(path, params=params, headers=H)
        checked += 1; statuses[r1.status_code] = statuses.get(r1.status_code, 0) + 1
        same = r1.status_code == r2.status_code and r1.content == r2.content and r1.headers.get("x-index-status") == r2.headers.get("x-index-status") and r1.headers.get("content-type") == r2.headers.get("content-type")
        if not same:
            mismatch += 1
            if mismatch <= 8: print("MISMATCH", path, params, r1.status_code, r2.status_code, r1.text[:300], "|||", r2.text[:300])
    # /health: same keys minus the indexer fields (AniServe has no indexer)
    h1 = norm_health(oc.get("/health").content); h2 = json.loads(nc.get("/health").content)
    print("health orig(no indexer keys) == new:", h1 == h2, h1, h2)
    # openapi: same routes and parameters; only title may differ
    o1 = oc.get("/openapi.json").json(); o2 = nc.get("/openapi.json").json()
    print("openapi paths equal:", o1["paths"] == o2["paths"], "| components equal:", o1.get("components") == o2.get("components"), "| title:", o1["info"]["title"], "->", o2["info"]["title"])
print(f"STRICT={new.STRICT} titles={len(IDS)} checked={checked} mismatches={mismatch} status codes={statuses}")
sys.exit(1 if mismatch else 0)
