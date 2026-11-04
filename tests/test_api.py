"""Kura API tests. Needs a database seeded with tools/seed_demo.py (or any real indexer catalogue):

  DATABASE_URL=postgresql://... python tests/test_api.py

Runs the app in-process (no server needed) and exits non-zero on the first failing group.
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("API_RATE_LIMIT_RPM", "0")
from fastapi.testclient import TestClient  # noqa: E402
import psycopg2  # noqa: E402
import app as K  # noqa: E402

fails = []
def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{extra}]" if not cond and extra else ""))
    if not cond: fails.append(name)

with TestClient(K.app) as c:
    g = lambda *a, **k: c.get(*a, **k)

    # --- shape of collections
    r = g("/popular?per_page=5").json()
    check("popular envelope", set(r) == {"page", "perPage", "total", "hasNextPage", "results"} and len(r["results"]) == 5)
    pops = [x["popularity"] for x in r["results"]]
    check("popular sorted desc", pops == sorted(pops, reverse=True))
    check("slim shape has no description/characters", all("description" not in x and "characters" not in x for x in r["results"]))
    check("strict shape: no cover color in lists", all("color" not in x["coverImage"] for x in r["results"]))
    ids1 = [x["id"] for x in g("/popular?per_page=10&page=1").json()["results"]]
    ids2 = [x["id"] for x in g("/popular?per_page=10&page=2").json()["results"]]
    check("pages never overlap", not set(ids1) & set(ids2))

    # --- filters and search
    r = g("/filter?genre=Drama&status=RELEASING&per_page=50").json()
    check("filter genre+status", r["results"] and all("Drama" in x["genres"] and x["status"] == "RELEASING" for x in r["results"]))
    check("filter is case-insensitive on status", g("/filter?status=releasing").json()["total"] == g("/filter?status=RELEASING").json()["total"])
    check("search any word order", g("/search?query=gakuen kaze").json()["total"] == g("/search?query=kaze gakuen").json()["total"] > 0)
    check("search treats % literally", g("/search?query=%25").json()["total"] == 0)
    check("bad sort is 400", g("/filter?sort=NOPE").status_code == 400)
    check("per_page over max is 422", g("/popular?per_page=51").status_code == 422)
    check("suggestions <= 8", 0 < len(g("/suggestions?query=a").json()["suggestions"]) <= 8)

    # --- adult handling
    with K.db.cursor() as cur:
        cur.execute("SELECT id FROM anilist_catalog WHERE is_adult LIMIT 1"); adult = cur.fetchone()
        cur.execute("SELECT id FROM anilist_catalog WHERE NOT is_adult AND status='RELEASING' LIMIT 1"); open_id = cur.fetchone()[0]
    if adult:
        check("adult title 404 by default", g(f"/info/{adult[0]}").status_code == 404)
        check("adult title visible with isAdult=true", g(f"/info/{adult[0]}?isAdult=true").status_code == 200)
    check("adult hidden from list totals", g("/list").json()["total"] < g("/list?isAdult=true").json()["total"] if adult else True)

    # --- detail routes
    info = g(f"/info/{open_id}").json()
    check("info has ordered top keys", list(info)[:3] == ["id", "idMal", "title"])
    check("info hides bookkeeping keys", "mainStudios" not in info and "_extrasAt" not in info)
    check("info recommendations capped at 10", len((info.get("recommendations") or {}).get("nodes", [])) <= 10)
    check("info 404 for unknown id", g("/info/1").status_code == 404)
    with K.db.cursor() as cur:
        cur.execute("SELECT id FROM anilist_detail_pages WHERE kind='characters' AND page=2 LIMIT 1"); paged = cur.fetchone()
    if paged:
        pid = paged[0]
        p1 = g(f"/anime/{pid}/characters?page=1&per_page=25").json(); p2 = g(f"/anime/{pid}/characters?page=2&per_page=25").json()
        check("characters page 2 served from stored block", len(p2["characters"]) > 0 and p1["total"] == p2["total"])
        check("characters pages differ", {e["node"]["id"] for e in p1["characters"]}.isdisjoint({e["node"]["id"] for e in p2["characters"]}))
        straddle = g(f"/anime/{pid}/characters?page=3&per_page=10").json()  # items 21-30 cross the block boundary
        check("window across two blocks", len(straddle["characters"]) == 10)
        check("page beyond stored data is empty, not an error", g(f"/anime/{pid}/characters?page=9&per_page=25").json()["characters"] == [])
    check("relations envelope", set(g(f"/anime/{open_id}/relations").json()) == {"id", "title", "relations"})
    check("recommendations envelope", "recommendations" in g(f"/anime/{open_id}/recommendations").json())

    # --- schedule: timeUntilAiring is recomputed, never the stored value
    s = g("/schedule?per_page=3").json()["results"]
    check("schedule has airing fields", s and all({"next_episode", "airingAt", "timeUntilAiring"} <= set(x) for x in s))
    check("schedule is soonest first", [x["airingAt"] for x in s] == sorted(x["airingAt"] for x in s))
    check("timeUntilAiring is fresh", all(abs(x["timeUntilAiring"] - max(0, x["airingAt"] - int(time.time()))) <= 2 for x in s))

    # --- kura extras
    st = g("/stats").json()
    check("stats counts add up", sum(x["count"] for x in st["status"]) == st["entries"] == g("/list").json()["total"])
    ex = g("/export").text.strip().split("\n")
    check("export ndjson has one record per line", len(ex) == st["entries"])
    import json as _j
    check("export json is one array", len(_j.loads(g("/export?format=json").text)) == st["entries"])
    check("export bad format is 422", g("/export?format=csv").status_code == 422)
    check("health online", g("/health").json()["status"] == "online")
    check("every response carries timing header", "x-kura-ms" in g("/genres").headers)
    check("genres hide Hentai by default", "Hentai" not in [x["name"] for x in g("/genres").json()["results"]] and "Hentai" in [x["name"] for x in g("/genres?isAdult=true").json()["results"]])

    # --- read-only: the database refuses writes even if the code tried
    try:
        with K.db.cursor() as cur: cur.execute("DELETE FROM anilist_catalog WHERE id = -1")
        check("database session is read-only", False)
    except psycopg2.errors.ReadOnlySqlTransaction:
        check("database session is read-only", True)
    except Exception as exc:
        check("database session is read-only", False, repr(exc))

    # --- outage behaviour: a dead connection is a clean 503, and the service recovers
    for conn in list(K.db.pool._pool): conn.close()
    K.db.used.clear()
    check("dead pooled connection recovers transparently", g("/popular?per_page=1").status_code == 200)

# --- rate limiter
os.environ["API_RATE_LIMIT_RPM"] = "3"; os.environ["API_RATE_LIMIT_WINDOW_SECONDS"] = "60"
K.api_limiter = K.ApiRateLimiter()
with TestClient(K.app) as c:
    codes = [c.get("/genres").status_code for _ in range(5)]
    check("rate limit returns 429 after the budget", codes[:3] == [200, 200, 200] and codes[3] == 429, str(codes))
    check("429 carries Retry-After", "retry-after" in c.get("/genres").headers)

print(f"\n{len(fails)} failed" if fails else "\nall passed")
sys.exit(1 if fails else 0)
