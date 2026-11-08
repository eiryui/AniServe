"""Deterministic fixture catalog for tests: realistic AniList records written with the same column mapping as the database dump.

  python3 tests/seed.py DATABASE_URL [COUNT]     # create the schema and fill a scratch database (its name must contain 'test')

Covers what the routes branch on: adult and non-adult titles, every status / format / season, titles without a score or dates,
airing schedules, relations and recommendations that point at indexed and unknown titles (some without cover colours), more than 25
characters and recommendations with some blocks indexed and some not, staff / links / stats on some titles, awkward characters in titles.
"""
import os, random, sys, time
from datetime import datetime, timezone
from pathlib import Path

import psycopg2, psycopg2.extras

ROOT = Path(__file__).resolve().parent.parent
GENRES = ["Action", "Adventure", "Comedy", "Drama", "Fantasy", "Horror", "Mahou Shoujo", "Mecha", "Music", "Mystery", "Psychological", "Romance", "Sci-Fi", "Slice of Life", "Sports", "Supernatural", "Thriller"]
TAGS = ["Isekai", "School", "Time Manipulation", "Ensemble Cast", "Male Protagonist", "Female Protagonist", "Primarily Adult Cast", "Swordplay", "Idol", "Cyberpunk"]
STATUSES = ["FINISHED", "FINISHED", "FINISHED", "RELEASING", "NOT_YET_RELEASED", "CANCELLED", "HIATUS"]
FORMATS = ["TV", "TV", "TV_SHORT", "MOVIE", "SPECIAL", "OVA", "ONA", "MUSIC"]
SEASONS = ["WINTER", "SPRING", "SUMMER", "FALL"]
WORDS = ["Shingeki", "Kyojin", "Re:Zero", "Sword", "Art", "Online", "Frieren", "Beyond", "Journey", "End", "Mushoku", "Tensei", "Kimi", "Todoke", "Fullmetal", "Alchemist", "Steins", "Gate", "Cowboy", "Bebop", "Mob", "Psycho", "Hunter", "Monster", "Naruto", "Bleach", "Spy", "Family", "Haikyuu", "Clannad"]
NOW = int(time.time())

def cover(i, color=True):
    c = {"large": f"https://img.example/c/{i}-l.jpg", "extraLarge": f"https://img.example/c/{i}-xl.jpg"}
    if color: c["color"] = ["#e45d50", "#50a3e4", "#6fe450", None, "#e4c850"][i % 5]
    return c

def title(rng, i):
    w = rng.sample(WORDS, 2)
    t = {"romaji": f"{w[0]} {w[1]} {i}", "english": f"The {w[1]} of {w[0]} {i}" if i % 4 else None, "native": f"日本語 {i}" if i % 3 else None}
    if i % 37 == 0: t["romaji"] = f"100% Pure_Wild \\ {w[0]} {i}"  # LIKE metacharacters
    return t

def media(rng, i, ids):
    status = rng.choice(STATUSES); year = rng.randint(1985, 2027) if i % 11 else None
    adult = i % 13 == 0
    start = {"year": year, "month": rng.choice([None, 1, 4, 7, 10]), "day": rng.choice([None, 3, 15, 28])} if year else {"year": None, "month": None, "day": None}
    if start["month"] is None: start["day"] = None
    airing = None
    if status == "RELEASING" and i % 2: airing = {"episode": rng.randint(2, 24), "airingAt": NOW + rng.randint(600, 6 * 86400), "timeUntilAiring": 1234}
    n_chars, n_recs = rng.choice([0, 3, 10, 25, 26, 40, 60, 80]), rng.choice([0, 4, 25, 26, 30, 55])
    def rec_node(j):
        t = ids[(i * 7 + j * 13) % len(ids)] if j % 9 != 8 else 9_000_000 + i * 100 + j
        return {"rating": 500 - j, "mediaRecommendation": {"id": t, "title": {"romaji": f"Rec {t}", "english": None, "native": None},
                "coverImage": {"large": f"https://img.example/c/{t}-l.jpg", "extraLarge": f"https://img.example/c/{t}-xl.jpg", **({"color": "#abcdef"} if j % 2 else {})},
                "bannerImage": None, "isAdult": t % 13 == 0 and t < 9_000_000, "format": "TV", "episodes": 12, "status": "FINISHED", "meanScore": 70 + j % 20,
                "averageScore": 71, "popularity": 1000 - j, "genres": ["Drama"], "startDate": {"year": 2001}}}
    def char_edge(j):
        return {"role": "MAIN" if j < 5 else "SUPPORTING", "node": {"id": i * 10000 + j, "name": {"full": f"Char {i}-{j}", "native": f"キャラ{j}", "userPreferred": f"Char {i}-{j}"}, "image": {"large": f"https://img.example/ch/{i}/{j}.jpg", "medium": f"https://img.example/ch/{i}/{j}m.jpg"}},
                "voiceActors": [{"id": i * 100 + j, "name": {"full": f"VA {i}-{j}", "native": "声優"}, "image": {"large": "https://img.example/va.jpg"}, "languageV2": "Japanese"},
                                {"id": i * 100 + j + 50, "name": {"full": f"EN VA {i}-{j}", "native": None}, "image": {"large": "https://img.example/va2.jpg"}, "languageV2": "English"}]}
    rel_edges = []
    for j in range(rng.choice([0, 1, 3, 6])):
        t = ids[(i * 5 + j * 11) % len(ids)] if j != 2 else 8_000_000 + i
        rel_edges.append({"relationType": rng.choice(["SEQUEL", "PREQUEL", "SIDE_STORY", "ADAPTATION"]), "node": {"id": t, "title": {"romaji": f"Rel {t}", "english": None, "native": None},
                          "coverImage": {"large": f"https://img.example/c/{t}-l.jpg", **({"color": "#112233"} if j % 2 else {})}, "bannerImage": None, "isAdult": t % 13 == 0 and t < 8_000_000,
                          "format": "TV" if j != 3 else "MANGA", "type": "ANIME" if j != 3 else "MANGA", "status": "FINISHED", "episodes": 12, "chapters": None, "meanScore": 72, "averageScore": 73, "popularity": 500, "startDate": {"year": 1999, "month": 4, "day": 1}}})
    studios = [{"id": i % 50, "name": f"Studio {i % 50}", "isAnimationStudio": True, "siteUrl": f"https://anilist.co/studio/{i % 50}"}, {"id": 900 + i % 5, "name": f"Producer {i % 5}", "isAnimationStudio": False, "siteUrl": None}]
    m = {"id": i, "idMal": i + 1000 if i % 2 else None, "title": title(rng, i), "description": f"Synopsis for {i}. <br>Second line.", "coverImage": cover(i, i % 7 != 0),
         "bannerImage": f"https://img.example/b/{i}.jpg" if i % 3 == 0 else None, "format": rng.choice(FORMATS), "season": rng.choice(SEASONS) if year else None, "seasonYear": year,
         "episodes": rng.choice([None, 1, 12, 13, 24, 50]), "duration": rng.choice([None, 24, 95]), "status": status,
         "averageScore": rng.choice([None, rng.randint(40, 95)]), "meanScore": rng.choice([None, rng.randint(40, 95)]), "popularity": rng.randint(10, 400000) if i % 17 else None,
         "favourites": rng.randint(0, 90000), "trending": rng.choice([0, 0, rng.randint(1, 900)]), "genres": rng.sample(GENRES, rng.randint(0, 4)) + (["Hentai"] if adult else []),
         "tags": [{"name": t, "rank": rng.randint(20, 100), "isMediaSpoiler": bool(rng.getrandbits(1))} for t in rng.sample(TAGS, rng.randint(0, 4))],
         "source": rng.choice(["ORIGINAL", "MANGA", "LIGHT_NOVEL", None]), "countryOfOrigin": "JP", "isAdult": adult, "hashtag": f"#t{i}" if i % 5 == 0 else None,
         "synonyms": [f"Alt Name {i}"] if i % 6 == 0 else [], "siteUrl": f"https://anilist.co/anime/{i}",
         "trailer": {"id": f"yt{i}", "site": "youtube", "thumbnail": "https://img.example/t.jpg"} if i % 4 == 0 else None,
         "studios": {"nodes": studios}, "updatedAt": NOW - rng.randint(0, 10_000_000), "nextAiringEpisode": airing, "startDate": start,
         "endDate": {"year": year + 1 if year and status == "FINISHED" else None, "month": None, "day": None},
         "relations": {"edges": rel_edges},
         "recommendations": {"pageInfo": {"total": n_recs, "currentPage": 1, "hasNextPage": n_recs > 25, "perPage": 25}, "nodes": [rec_node(j) for j in range(min(25, n_recs))]},
         "characters": {"pageInfo": {"total": n_chars, "currentPage": 1, "hasNextPage": n_chars > 25, "perPage": 25}, "edges": [char_edge(j) for j in range(min(25, n_chars))]}}
    if i % 3: m["mainStudios"] = {"nodes": [{"name": studios[0]["name"], "isAnimationStudio": True}]}  # the rest exercise the animation-studio fallback
    if i % 4 == 0:  # the extras job has been through this title
        m.update({"staff": {"edges": [{"role": "Director", "node": {"id": i * 10 + 1, "name": {"full": f"Staff {i}", "native": "監督"}, "image": {"large": "https://img.example/s.jpg"}}}]},
                  "externalLinks": [{"url": f"https://example.org/{i}", "site": "Official Site", "type": "INFO"}],
                  "streamingEpisodes": [{"title": f"Episode 1 of {i}", "thumbnail": "https://img.example/e.jpg", "url": f"https://stream.example/{i}/1", "site": "Crunchyroll"}],
                  "stats": {"scoreDistribution": [{"score": 80, "amount": i % 50 + 1}], "statusDistribution": [{"status": "CURRENT", "amount": i % 70 + 1}]}, "_extrasAt": NOW - 3600})
    return m, char_edge, rec_node

def row(x):
    t = x.get("title") or {}; tags = [g["name"] for g in x.get("tags") or [] if g.get("name")]; next_at = (x.get("nextAiringEpisode") or {}).get("airingAt")
    text = " ".join(str(v) for v in [t.get("romaji"), t.get("english"), t.get("native"), *(x.get("synonyms") or [])] if v)
    return (x["id"], psycopg2.extras.Json(x), text, x.get("status"), x.get("seasonYear"), x.get("season"), x.get("format"), x.get("popularity"), x.get("trending"), x.get("meanScore"),
            x.get("genres") or [], tags, datetime.fromtimestamp(next_at, timezone.utc) if next_at else None, None)

def seed(conn, count=240):
    rng = random.Random(20260801)
    ids = list(range(100, 100 + count * 3, 3))
    with conn.cursor() as c:
        c.execute("SELECT current_database()"); name = c.fetchone()[0]
        if "test" not in name.lower() and os.getenv("ANISERVE_ALLOW_SEED") != "1":  # seeding EMPTIES the tables: never do it to a real database by accident
            raise SystemExit(f"refusing to seed database {name!r}: seeding truncates the catalog tables. Use a scratch database with 'test' in its name, or set ANISERVE_ALLOW_SEED=1.")
        c.execute((ROOT / "schema.sql").read_text(encoding="utf-8"))
        c.execute("TRUNCATE anilist_catalog, anilist_detail_pages, anilist_index_state")
        rows, pages = [], []
        for n, i in enumerate(ids):
            m, char_edge, rec_node = media(rng, i, ids); rows.append(row(m))
            for kind, key, mk in (("characters", "edges", char_edge), ("recommendations", "nodes", rec_node)):
                total = m[kind]["pageInfo"]["total"]
                for page in range(2, -(-total // 25) + 1):  # every third title has all blocks, every other has none, the rest only block 2
                    if n % 3 == 0 or (n % 3 == 1 and page == 2):
                        pages.append((i, kind, page, psycopg2.extras.Json([mk(j) for j in range((page - 1) * 25, min(page * 25, total))]), total))
        psycopg2.extras.execute_values(c, "INSERT INTO anilist_catalog (id,payload,search_text,status,season_year,season,format,popularity,trending,mean_score,genres,tags,next_airing_at,refresh_after) VALUES %s", rows)
        if pages: psycopg2.extras.execute_values(c, "INSERT INTO anilist_detail_pages (id,kind,page,items,total) VALUES %s", pages)
        c.execute("INSERT INTO anilist_index_state(key,value) VALUES ('initial','{\"ok\": true}')")
    conn.commit()
    return ids

if __name__ == "__main__":
    conn = psycopg2.connect(sys.argv[1]); ids = seed(conn, int(sys.argv[2]) if len(sys.argv) > 2 else 240); print(f"seeded {len(ids)} titles")
