"""AniServe tests. Self-contained: builds demo data in every supported file format and runs the same checks against each engine.

  pip install -r requirements-dev.txt
  python tests/test_api.py                                  SQLite, DuckDB (memory and disk) and Parquet, from generated demo data
  TEST_POSTGRES_URL=postgresql://... python tests/test_api.py    also PostgreSQL (a database holding the demo data or a real restore)

Exit code is non-zero if any check fails.
"""
import hashlib, json, os, subprocess, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["API_RATE_LIMIT_RPM"] = "0"
os.environ.pop("DATABASE_URL", None)
from fastapi.testclient import TestClient  # noqa: E402
import app as A  # noqa: E402
import load_dump  # noqa: E402
import stores  # noqa: E402

fails, total = [], 0


def check(name, cond, extra=""):
    global total
    total += 1
    if not cond:
        fails.append(name); print("  FAIL " + name + (f"  [{extra}]" if extra else ""))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_fixtures(tmp):
    """Demo data -> SQLite (direct), DuckDB (via the JSON Lines converter), Parquet (written by DuckDB)."""
    sqlite_file = tmp / "demo.sqlite"
    subprocess.run([sys.executable, "-I", str(ROOT / "tools" / "make_demo.py"), "--count", "240", "--out", str(sqlite_file), "--jsonl", str(tmp / "jsonl")], check=True, capture_output=True)
    duck_file = tmp / "demo.duckdb"
    subprocess.run([sys.executable, "-I", str(ROOT / "load_dump.py"), str(tmp / "jsonl"), "--to", "duckdb", "--out", str(duck_file)], check=True, capture_output=True)
    import duckdb
    pq = tmp / "parquet"; pq.mkdir()
    con = duckdb.connect(str(duck_file), read_only=True)
    for t in load_dump.TABLES:
        con.execute(f"COPY public.{t} TO '{pq}/public.{t}.parquet' (FORMAT parquet)")
    con.close()
    return sqlite_file, duck_file, pq


def suite(label, url, writable_probe=None):
    print(f"{label}")
    A.catalog = stores.Catalog(url); A._stats_cache.clear()
    with TestClient(A.app) as c:
        g = c.get
        check("health online", g("/health").json()["status"] == "online" and g("/health").json()["mode"] == "read-only", g("/health").text)

        # --- collections
        r = g("/popular?per_page=5").json()
        check("popular envelope", set(r) == {"page", "perPage", "total", "hasNextPage", "results"} and len(r["results"]) == 5)
        pops = [x["popularity"] for x in r["results"]]
        check("popular sorted desc", pops == sorted(pops, reverse=True))
        check("slim shape", all("description" not in x and "characters" not in x for x in r["results"]))
        check("strict shape drops cover colour in lists", all("color" not in x["coverImage"] for x in r["results"]))
        a = [x["id"] for x in g("/popular?per_page=10&page=1").json()["results"]]; b = [x["id"] for x in g("/popular?per_page=10&page=2").json()["results"]]
        check("pages never overlap", not set(a) & set(b))

        # --- filters, search, sort
        r = g("/filter?genre=Drama&status=RELEASING&per_page=50").json()
        check("filter genre + status", r["total"] > 0 and all("Drama" in x["genres"] and x["status"] == "RELEASING" for x in r["results"]))
        two = g("/filter?genre=Drama&genre=Fantasy&per_page=50").json()["results"]
        check("several genres must all match", all({"Drama", "Fantasy"} <= set(x["genres"]) for x in two))
        check("filter tag", g("/filter?tag=Shogi").json()["total"] >= 0 and all(any(t["name"] == "Shogi" for t in x["tags"]) for x in g("/filter?tag=Shogi&per_page=50").json()["results"]))
        check("status filter ignores case", g("/filter?status=releasing").json()["total"] == g("/filter?status=RELEASING").json()["total"] > 0)
        check("search matches any word order", g("/search?query=gakuen kaze").json()["total"] == g("/search?query=kaze gakuen").json()["total"])
        check("search is case-insensitive", g("/search?query=HOSHI").json()["total"] == g("/search?query=hoshi").json()["total"] > 0)
        check("search finds Japanese text", g("/search?query=（仮）").json()["total"] > 0)
        check("search treats % and _ literally", g("/search?query=%25").json()["total"] == 0 and g("/search?query=a_b").json()["total"] == 0)
        check("bad sort is 400", g("/filter?sort=NOPE").status_code == 400)
        check("per_page over max is 422", g("/popular?per_page=51").status_code == 422)
        check("suggestions at most 8", 0 < len(g("/suggestions?query=a").json()["suggestions"]) <= 8)
        for s in ("POPULARITY", "SCORE", "TRENDING", "START_DATE", "FAVOURITES", "UPDATED_AT", "ID"):
            x1 = [i["id"] for i in g(f"/filter?sort={s}_DESC&per_page=20").json()["results"]]
            x2 = [i["id"] for i in g(f"/filter?sort={s}_DESC&per_page=20&page=2").json()["results"]]
            check(f"sort {s} deterministic and non-overlapping", len(set(x1 + x2)) == len(x1 + x2) == 40 and x1 == [i["id"] for i in g(f"/filter?sort={s}_DESC&per_page=20").json()["results"]])
        score = [i["averageScore"] for i in g("/filter?sort=SCORE_DESC&per_page=50").json()["results"] if i.get("averageScore") is not None]
        check("SCORE_DESC orders by score", score == sorted(score, reverse=True))

        # --- adult handling
        every = g("/list?ids_only=true&per_page=500&isAdult=true").json()["results"]
        public = g("/list?ids_only=true&per_page=500").json()["results"]
        adult = sorted(set(every) - set(public))
        check("some titles are adult in the test data", len(adult) > 0)
        if adult:
            check("adult title is 404 by default", g(f"/info/{adult[0]}").status_code == 404)
            check("adult title shows with isAdult=true", g(f"/info/{adult[0]}?isAdult=true").status_code == 200)
            linked = [e["node"]["id"] for i in public[:60] for e in (g(f"/anime/{i}/relations").json()["relations"])]
            check("adult titles never appear in relations", not set(linked) & set(adult))
        check("genres hide Hentai unless asked", "Hentai" not in [x["name"] for x in g("/genres").json()["results"]] and "Hentai" in [x["name"] for x in g("/genres?isAdult=true").json()["results"]])

        # --- detail pages
        rel = g("/recent?per_page=1").json()["results"][0]["id"]
        info = g(f"/info/{rel}").json()
        check("info keys in fixed order", list(info)[:3] == ["id", "idMal", "title"])
        check("info hides bookkeeping keys", "mainStudios" not in info and "_extrasAt" not in info)
        check("info recommendations capped at 10", len((info.get("recommendations") or {}).get("nodes", [])) <= 10)
        check("info only lists Japanese voice actors", all(v["languageV2"] == "Japanese" for e in info["characters"]["edges"] for v in e["voiceActors"]))
        check("info 404 for unknown id", g("/info/1").status_code == 404)
        st = A.catalog.store
        paged = next((i for i in public if st.blocks(i, "characters", 2, 2)), None)
        check("test data has a stored second page of characters", paged is not None)
        if paged:
            p1 = g(f"/anime/{paged}/characters?page=1&per_page=25").json(); p2 = g(f"/anime/{paged}/characters?page=2&per_page=25").json()
            check("characters page 2 comes from the stored block", len(p2["characters"]) > 0 and p1["total"] == p2["total"])
            check("character pages do not overlap", {e["node"]["id"] for e in p1["characters"]}.isdisjoint({e["node"]["id"] for e in p2["characters"]}))
            check("a window can span two stored blocks", len(g(f"/anime/{paged}/characters?page=3&per_page=10").json()["characters"]) == 10)
            check("a page past the stored data is empty, not an error", g(f"/anime/{paged}/characters?page=9&per_page=25").json()["characters"] == [])
        check("relations envelope", set(g(f"/anime/{rel}/relations").json()) == {"id", "title", "relations"})
        check("recommendations envelope", "recommendations" in g(f"/anime/{rel}/recommendations").json())

        # --- schedule
        s = g("/schedule?per_page=5").json()["results"]
        check("schedule has airing fields", s and all({"next_episode", "airingAt", "timeUntilAiring"} <= set(x) for x in s))
        check("schedule is soonest first", [x["airingAt"] for x in s] == sorted(x["airingAt"] for x in s))
        check("timeUntilAiring is recomputed", all(abs(x["timeUntilAiring"] - max(0, x["airingAt"] - int(time.time()))) <= 2 for x in s))

        # --- stats and export
        stt = g("/stats").json()
        check("stats counts add up", sum(x["count"] for x in stt["status"]) == stt["entries"] == g("/list").json()["total"])
        check("stats has timestamps and jobs in UTC", stt["lastIndexedAt"].endswith("+00:00") and len(stt["refreshJobs"]) > 0 and all(v.endswith("+00:00") for v in stt["refreshJobs"].values()), str(stt["lastIndexedAt"]))
        check("stats has decades and genres", len(stt["decades"]) > 1 and 0 < len(stt["genres"]) <= 12)
        lines = g("/export").text.strip().split("\n")
        check("export ndjson: one record per line", len(lines) == stt["entries"] and json.loads(lines[0])["id"] < json.loads(lines[-1])["id"])
        check("export json: one array", len(json.loads(g("/export?format=json").text)) == stt["entries"])
        check("export matches /list record for record", [json.loads(x) for x in lines[:5]] == g("/list?per_page=5").json()["results"])
        check("export rejects other formats", g("/export?format=csv").status_code == 422)
        check("every response has timing header", "x-aniserve-ms" in g("/genres").headers)

    # --- the data itself is never written
    if writable_probe: writable_probe()


def outage_checks(url):
    print("PostgreSQL outage behaviour")
    A.catalog = stores.Catalog(url); A._stats_cache.clear()
    with TestClient(A.app) as c:
        check("serves before the outage", c.get("/popular?per_page=1").status_code == 200)
        for conn in list(A.catalog.store.pool._pool): conn.close()
        A.catalog.store.used.clear()
        check("dead pooled connections are replaced transparently", c.get("/popular?per_page=1").status_code == 200)
        try:
            with A.catalog.store.pool.getconn() as conn:
                conn.readonly = True
                with conn.cursor() as cur: cur.execute("DELETE FROM anilist_catalog WHERE id = -1")
            check("PostgreSQL transaction is read-only", False)
        except Exception as exc:
            check("PostgreSQL transaction is read-only", "read-only" in str(exc).lower(), repr(exc))


def error_checks(tmp):
    print("Configuration errors")
    for label, url, needle in [("missing file", "sqlite:///nope/x.sqlite", "not found"), ("unsupported", "mysql://u@h/db", "DATABASE_URL must start with"), ("empty", "", "DATABASE_URL is not set"),
                               ("wrong absolute-path slashes", f"sqlite://{tmp}/demo.sqlite", "four slashes")]:
        A.catalog = stores.Catalog(url)
        with TestClient(A.app) as c:
            r = c.get("/popular"); h = c.get("/health").json()
            check(f"{label}: 503 with a reason", r.status_code == 503 and needle in r.json()["detail"], r.text[:150])
            check(f"{label}: /health is degraded and says why", h["status"] == "degraded" and needle in h.get("problem", ""), str(h))
    # a database that is not a catalogue
    import sqlite3
    bad = tmp / "empty.sqlite"; sqlite3.connect(bad).execute("CREATE TABLE other(x)").connection.commit()
    A.catalog = stores.Catalog(f"sqlite:///{bad}".replace("sqlite:///", "sqlite:////", 1) if str(bad).startswith("/") else f"sqlite:///{bad}")
    with TestClient(A.app) as c:
        check("a database without the catalogue table is reported clearly", "anilist_catalog" in c.get("/health").json().get("problem", ""), c.get("/health").text)


def converter_checks():
    print("Converter (load_dump.py)")
    p = load_dump.parse_pg_array
    check("pg array: plain", p("{Action,Drama}") == ["Action", "Drama"])
    check("pg array: quoted with spaces", p('{"Slice of Life",Action}') == ["Slice of Life", "Action"])
    check("pg array: comma, quote and backslash inside", p(r'{"a,b","say \"hi\"","back\\slash"}') == ["a,b", 'say "hi"', "back\\slash"])
    check("pg array: empty and NULL", p("{}") == [] and p("{NULL,x}") == [None, "x"] and p("") is None)
    check("timestamps become UTC ISO", load_dump.parse_time("2026-11-02 05:00:00.5+00") == "2026-11-02T05:00:00.500000+00:00" and load_dump.parse_time("2026-11-02T11:00:00+06:00") == "2026-11-02T05:00:00+00:00")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        sqlite_file, duck_file, pq = build_fixtures(tmp)
        before = {p: sha(p) for p in [sqlite_file, duck_file, *pq.glob("*.parquet")]}
        suite("SQLite", f"sqlite:///{sqlite_file}".replace("sqlite:///", "sqlite:////", 1))
        os.environ["ANISERVE_DUCKDB_MODE"] = "memory"; suite("DuckDB (memory)", f"duckdb:///{duck_file}".replace("duckdb:///", "duckdb:////", 1))
        os.environ["ANISERVE_DUCKDB_MODE"] = "disk"; suite("DuckDB (disk)", f"duckdb:///{duck_file}".replace("duckdb:///", "duckdb:////", 1))
        suite("Parquet (disk)", f"parquet:///{pq}".replace("parquet:///", "parquet:////", 1))
        os.environ["ANISERVE_DUCKDB_MODE"] = "memory"; suite("Parquet (memory)", f"parquet:///{pq}".replace("parquet:///", "parquet:////", 1))
        check("source files are byte-for-byte unchanged", before == {p: sha(p) for p in before})
        if os.getenv("TEST_POSTGRES_URL"):
            suite("PostgreSQL", os.environ["TEST_POSTGRES_URL"]); outage_checks(os.environ["TEST_POSTGRES_URL"])
        error_checks(tmp); converter_checks()
        # rate limiter
        os.environ["API_RATE_LIMIT_RPM"] = "3"; A.api_limiter = A.ApiRateLimiter()
        A.catalog = stores.Catalog(f"sqlite:///{sqlite_file}".replace("sqlite:///", "sqlite:////", 1))
        with TestClient(A.app) as c:
            codes = [c.get("/genres").status_code for _ in range(5)]
            check("rate limit returns 429 after the budget", codes[:3] == [200, 200, 200] and codes[3] == 429, str(codes))
            check("429 carries Retry-After", "retry-after" in c.get("/genres").headers)
    print(f"\n{total - len(fails)}/{total} checks passed" + (f", {len(fails)} FAILED: {fails}" if fails else ""))
    sys.exit(1 if fails else 0)
