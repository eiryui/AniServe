"""Fill a database with invented demo titles so Kura can be tried without running an AniList Indexer.

  python tools/seed_demo.py                 uses DATABASE_URL
  python tools/seed_demo.py --count 300

Creates the same tables the AniList Indexer creates (anilist_catalog and friends) if they are missing,
then inserts fictional titles with generated SVG posters. Run it only against a throwaway database:
a real catalog written by the indexer would be mixed with these records (ids 9,000,001 and up).
This is the only Kura script that writes to the database.
"""
import argparse, base64, json, os, random, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg2, psycopg2.extras
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Same DDL as the AniList Indexer (1.8). Kept here so the demo works on an empty database.
SCHEMA = """
CREATE TABLE IF NOT EXISTS anilist_catalog (
 id INTEGER PRIMARY KEY, payload JSONB NOT NULL, search_text TEXT NOT NULL, status TEXT,
 season_year INTEGER, season TEXT, format TEXT, popularity INTEGER, trending INTEGER,
 mean_score INTEGER, genres TEXT[] NOT NULL DEFAULT '{}', tags TEXT[] NOT NULL DEFAULT '{}',
 next_airing_at TIMESTAMPTZ, refresh_after TIMESTAMPTZ, indexed_at TIMESTAMPTZ NOT NULL DEFAULT now());
ALTER TABLE anilist_catalog ADD COLUMN IF NOT EXISTS is_adult BOOLEAN GENERATED ALWAYS AS (COALESCE((payload->>'isAdult')::boolean, false)) STORED;
ALTER TABLE anilist_catalog ADD COLUMN IF NOT EXISTS favourites INTEGER GENERATED ALWAYS AS ((payload->>'favourites')::int) STORED;
ALTER TABLE anilist_catalog ADD COLUMN IF NOT EXISTS average_score INTEGER GENERATED ALWAYS AS ((payload->>'averageScore')::int) STORED;
ALTER TABLE anilist_catalog ADD COLUMN IF NOT EXISTS media_updated_at BIGINT GENERATED ALWAYS AS ((payload->>'updatedAt')::bigint) STORED;
ALTER TABLE anilist_catalog ADD COLUMN IF NOT EXISTS start_key INTEGER GENERATED ALWAYS AS (CASE WHEN payload#>>'{startDate,year}' IS NULL THEN NULL ELSE (payload#>>'{startDate,year}')::int*10000 + COALESCE((payload#>>'{startDate,month}')::int,0)*100 + COALESCE((payload#>>'{startDate,day}')::int,0) END) STORED;
CREATE INDEX IF NOT EXISTS anilist_catalog_airing_idx ON anilist_catalog(next_airing_at);
CREATE INDEX IF NOT EXISTS anilist_catalog_status_idx ON anilist_catalog(status);
CREATE INDEX IF NOT EXISTS anilist_catalog_year_idx ON anilist_catalog(season_year);
CREATE INDEX IF NOT EXISTS anilist_catalog_genres_idx ON anilist_catalog USING GIN(genres);
CREATE INDEX IF NOT EXISTS anilist_catalog_tags_idx ON anilist_catalog USING GIN(tags);
CREATE TABLE IF NOT EXISTS anilist_missing_ids (id INTEGER PRIMARY KEY, checked_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS anilist_index_state (key TEXT PRIMARY KEY, value JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS anilist_detail_pages (id INTEGER NOT NULL, kind TEXT NOT NULL, page INTEGER NOT NULL, items JSONB NOT NULL, total INTEGER, fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (id, kind, page));
"""

FIRST = ["Hoshi", "Kaze", "Yume", "Tsuki", "Nami", "Sora", "Kumo", "Hana", "Ryu", "Kage", "Akari", "Mizu", "Tora", "Yuki", "Kitsune", "Sakura", "Tetsu", "Asa", "Yoru", "Kohaku"]
SECOND = ["no Tabi", "Monogatari", "Senki", "Kyoushitsu", "no Kanata", "Gakuen", "Tenshi", "Kaidan", "no Machi", "Kiroku", "Rondo", "Densetsu", "Zankyou", "no Shiro"]
ENGLISH = ["Starlit Voyage", "The Quiet Archive", "Wind Cartographers", "Moonlit Courier", "Tidebound", "Paper Lantern Club", "Last Train to Kumo", "Ink & Ember", "Fox Hours", "The Salt Kingdom", "Clockwork Sparrow", "Orbit Tea House", "Hollow Peak Academy", "Sunday Revenants", "Glass Harbor"]
STUDIOS = ["Studio Hanabi", "Mokuren Works", "Tsuru Pictures", "Orange Kiln", "Lantern House", "Pale Ink", "Studio Mizutama"]
CHAR_FIRST = ["Aoi", "Ren", "Mika", "Daichi", "Sena", "Hiro", "Nao", "Ibuki", "Rin", "Kou", "Mio", "Shun"]
CHAR_LAST = ["Tachibana", "Mori", "Kuroda", "Hoshino", "Asagi", "Fujiwara", "Segawa", "Nishimura", "Kagami", "Onishi"]
PALETTE = ["#e86a5c", "#5ca8e8", "#e8c35c", "#7be8a0", "#b07be8", "#e87bb0", "#5ce8d8", "#e8905c", "#8ea0ff", "#a3c85a"]
FORMATS = ["TV", "TV", "TV", "MOVIE", "OVA", "ONA", "TV_SHORT", "SPECIAL"]
SOURCES = ["ORIGINAL", "MANGA", "LIGHT_NOVEL", "VISUAL_NOVEL", "GAME", "WEB_NOVEL"]
GENRES = ["Action", "Adventure", "Comedy", "Drama", "Fantasy", "Horror", "Mecha", "Music", "Mystery", "Psychological", "Romance", "Sci-Fi", "Slice of Life", "Sports", "Supernatural", "Thriller"]


def poster(title, color, size=(460, 650)):
    """A small SVG poster as a data URI, so the demo needs no network for images."""
    w, h = size
    mark = "".join(w_[0] for w_ in title.split()[:2]).upper()
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
           f'<stop offset="0" stop-color="{color}"/><stop offset="1" stop-color="#14120e"/></linearGradient></defs>'
           f'<rect width="{w}" height="{h}" fill="url(#g)"/><circle cx="{w*0.72:.0f}" cy="{h*0.28:.0f}" r="{w*0.2:.0f}" fill="#fff" fill-opacity=".14"/>'
           f'<path d="M0 {h*0.74:.0f} Q{w*0.3:.0f} {h*0.6:.0f} {w*0.6:.0f} {h*0.76:.0f} T{w} {h*0.7:.0f} V{h} H0Z" fill="#000" fill-opacity=".28"/>'
           f'<text x="{w*0.08:.0f}" y="{h*0.92:.0f}" font-family="Georgia,serif" font-size="{w*0.26:.0f}" fill="#fff" fill-opacity=".9">{mark}</text></svg>')
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


def date(rng, y):
    return {"year": y, "month": rng.randint(1, 12), "day": rng.randint(1, 28)}


def build(rng, i, ids, tags_pool, now):
    nid = 9_000_001 + i
    romaji = f"{rng.choice(FIRST)} {rng.choice(SECOND)}"
    english = rng.choice(ENGLISH) if rng.random() < 0.7 else None
    name = english or romaji
    year = rng.choice([*range(1988, 2027), *range(2018, 2027)])
    status = "FINISHED" if year < 2025 else rng.choice(["RELEASING", "RELEASING", "NOT_YET_RELEASED", "FINISHED"])
    if year >= 2027: status = "NOT_YET_RELEASED"
    fmt = rng.choice(FORMATS)
    season = rng.choice(["WINTER", "SPRING", "SUMMER", "FALL"])
    eps = 1 if fmt == "MOVIE" else rng.choice([12, 12, 13, 24, 25, 26, 6, 3])
    color = rng.choice(PALETTE)
    pop = int(rng.paretovariate(1.1) * 900)
    score = None if status == "NOT_YET_RELEASED" else rng.randint(52, 91)
    adult = rng.random() < 0.05
    genres = rng.sample(GENRES, rng.randint(1, 4))
    tags = [{"name": t, "rank": rng.randint(30, 98), "isMediaSpoiler": False} for t in rng.sample(tags_pool, rng.randint(2, 6))]
    nxt = None
    if status == "RELEASING":
        at = int((now + timedelta(hours=rng.randint(2, 160))).timestamp())
        nxt = {"episode": rng.randint(2, eps), "airingAt": at, "timeUntilAiring": at - int(now.timestamp())}
    cover = {"large": poster(name, color), "extraLarge": poster(name, color, (690, 975)), "color": color}
    chars_total = rng.choice([8, 14, 25, 25, 34, 52])
    def person(n): return {"id": 700_000 + n, "name": {"full": f"{rng.choice(CHAR_FIRST)} {rng.choice(CHAR_LAST)}", "native": "", "userPreferred": ""}, "image": {"large": None, "medium": None}}
    chars = [{"role": "MAIN" if k < 3 else "SUPPORTING", "node": person(nid % 997 * 100 + k), "voiceActors": [{"id": 5_000 + k, "name": {"full": f"{rng.choice(CHAR_FIRST)} {rng.choice(CHAR_LAST)}", "native": ""}, "image": {"large": None}, "languageV2": "Japanese"}]} for k in range(min(25, chars_total))]
    others = [j for j in ids if j != nid]
    def ref(j):
        return {"id": j, "title": {"romaji": f"Linked {j - 9_000_000}", "english": None, "native": None}, "coverImage": {"large": poster(f"L{j % 100}", rng.choice(PALETTE)), "color": rng.choice(PALETTE)}, "bannerImage": None, "isAdult": False, "format": "TV", "type": "ANIME", "status": "FINISHED", "episodes": 12, "chapters": None, "meanScore": 70, "averageScore": 70, "popularity": 1200, "startDate": {"year": 2015, "month": 4, "day": 1}}
    rel = [{"relationType": rng.choice(["SEQUEL", "PREQUEL", "SIDE_STORY", "SPIN_OFF"]), "node": ref(j)} for j in rng.sample(others, rng.randint(0, 3))]
    recs = [{"rating": rng.randint(1, 90), "mediaRecommendation": {k: v for k, v in ref(j).items() if k not in ("type", "chapters")} | {"genres": ["Drama"]}} for j in rng.sample(others, min(len(others), rng.choice([4, 10, 25])))]
    payload = {
        "id": nid, "idMal": None, "title": {"romaji": romaji, "english": english, "native": f"{romaji}（仮）"},
        "description": f"{name} is a fictional demo title generated by Kura's seed script. It follows a small cast through {eps} episodes of invented adventures.",
        "coverImage": cover, "bannerImage": None, "format": fmt, "season": season, "seasonYear": year, "episodes": eps, "duration": 24 if fmt != "MOVIE" else 105,
        "status": status, "averageScore": score, "meanScore": score, "popularity": pop, "favourites": pop // 9, "trending": rng.choice([0, 0, 0, rng.randint(1, 400)]),
        "genres": genres, "tags": tags, "source": rng.choice(SOURCES), "countryOfOrigin": "JP", "isAdult": adult, "hashtag": None, "synonyms": [],
        "siteUrl": None, "trailer": None, "studios": {"nodes": [{"id": 1, "name": rng.choice(STUDIOS), "isAnimationStudio": True, "siteUrl": None}]},
        "mainStudios": {"nodes": [{"name": rng.choice(STUDIOS), "isAnimationStudio": True}]}, "updatedAt": int(now.timestamp()) - rng.randint(0, 900_000),
        "nextAiringEpisode": nxt, "startDate": date(rng, year), "endDate": date(rng, year) if status == "FINISHED" else {"year": None, "month": None, "day": None},
        "relations": {"edges": rel}, "recommendations": {"pageInfo": {"total": len(recs), "currentPage": 1, "hasNextPage": False, "perPage": 25}, "nodes": recs},
        "characters": {"pageInfo": {"total": chars_total, "currentPage": 1, "hasNextPage": chars_total > 25, "perPage": 25}, "edges": chars},
    }
    text = " ".join(v for v in (romaji, english, payload["title"]["native"]) if v)
    refresh = now + timedelta(hours=24) if status != "FINISHED" else None
    return nid, payload, (nid, psycopg2.extras.Json(payload), text, status, year, season, fmt, pop, payload["trending"], score, genres, [t["name"] for t in tags],
                          datetime.fromtimestamp(nxt["airingAt"], timezone.utc) if nxt else None, refresh), chars_total


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--count", type=int, default=180)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    url = os.getenv("DATABASE_URL")
    if not url: sys.exit("error: set DATABASE_URL")
    src = (ROOT / "genres-tags.js").read_text(encoding="utf-8")
    start = src.index("const TAGS = ") + len("const TAGS = ")
    tags_pool = json.loads(src[start:src.index(";", start)])
    rng = random.Random(a.seed); now = datetime.now(timezone.utc)
    ids = [9_000_001 + i for i in range(a.count)]
    conn = psycopg2.connect(url); cur = conn.cursor(); cur.execute(SCHEMA)
    rows, paged = [], []
    for i in range(a.count):
        nid, payload, row, chars_total = build(rng, i, ids, tags_pool, now)
        rows.append(row)
        if chars_total > 25: paged.append((nid, payload["characters"]["edges"][0], chars_total))
    psycopg2.extras.execute_values(cur, """INSERT INTO anilist_catalog (id,payload,search_text,status,season_year,season,format,popularity,trending,mean_score,genres,tags,next_airing_at,refresh_after)
        VALUES %s ON CONFLICT (id) DO UPDATE SET payload=EXCLUDED.payload,search_text=EXCLUDED.search_text,status=EXCLUDED.status,season_year=EXCLUDED.season_year,season=EXCLUDED.season,format=EXCLUDED.format,
        popularity=EXCLUDED.popularity,trending=EXCLUDED.trending,mean_score=EXCLUDED.mean_score,genres=EXCLUDED.genres,tags=EXCLUDED.tags,next_airing_at=EXCLUDED.next_airing_at,refresh_after=EXCLUDED.refresh_after""", rows)
    for nid, sample, total in paged:  # a stored page 2, as the indexer's /anime/{id}/characters would have left behind
        items = [{**sample, "node": {**sample["node"], "id": 800_000 + nid % 1000 * 100 + k}} for k in range(min(25, total - 25))]
        cur.execute("INSERT INTO anilist_detail_pages(id,kind,page,items,total) VALUES(%s,'characters',2,%s,%s) ON CONFLICT(id,kind,page) DO UPDATE SET items=EXCLUDED.items,total=EXCLUDED.total", (nid, psycopg2.extras.Json(items), total))
    for key in ("initial", "recent", "schedule", "rank_popular", "rank_trending", "links"):
        cur.execute("INSERT INTO anilist_index_state(key,value,updated_at) VALUES(%s,'{\"ok\":true}',now()-%s*interval '1 hour') ON CONFLICT(key) DO UPDATE SET updated_at=EXCLUDED.updated_at", (key, random.Random(key).randint(1, 20)))
    conn.commit(); conn.close()
    print(f"seeded {a.count} demo titles ({len(paged)} with a stored second page of characters)")


if __name__ == "__main__":
    main()
