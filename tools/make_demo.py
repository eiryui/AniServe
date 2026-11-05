"""Generate a small demo catalogue of invented titles, so AniServe can be tried without downloading the real dump.

  python tools/make_demo.py                        writes data/demo.sqlite (serve it with DATABASE_URL=sqlite:///data/demo.sqlite)
  python tools/make_demo.py --count 18000         about the size of the real dump, for load testing
  python tools/make_demo.py --jsonl demo_jsonl/   also writes the release's JSON Lines layout (convert it with load_dump.py)

All titles, characters and posters are made up (posters are generated SVG data URIs, so no network is needed).
Ids start at 9,000,001, clear of real AniList ids.
"""
import argparse, base64, json, random, sqlite3, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from load_dump import INDEXES, TABLES  # noqa: E402

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
        nxt = {"episode": rng.randint(1, max(1, eps)), "airingAt": at, "timeUntilAiring": at - int(now.timestamp())}
    cover = {"large": poster(name, color), "extraLarge": poster(name, color, (690, 975)), "color": color}
    chars_total = rng.choice([8, 14, 25, 25, 34, 52])
    def person(n): return {"id": 700_000 + n, "name": {"full": f"{rng.choice(CHAR_FIRST)} {rng.choice(CHAR_LAST)}", "native": "", "userPreferred": ""}, "image": {"large": None, "medium": None}}
    chars = [{"role": "MAIN" if k < 3 else "SUPPORTING", "node": person(nid % 997 * 100 + k), "voiceActors": [{"id": 5_000 + k, "name": {"full": f"{rng.choice(CHAR_FIRST)} {rng.choice(CHAR_LAST)}", "native": ""}, "image": {"large": None}, "languageV2": "Japanese"}]} for k in range(min(25, chars_total))]
    def pick(k): return [j for j in rng.sample(ids, min(len(ids), k + 1)) if j != nid][:k]  # never the title itself
    def ref(j):
        return {"id": j, "title": {"romaji": f"Linked {j - 9_000_000}", "english": None, "native": None}, "coverImage": {"large": poster(f"L{j % 100}", rng.choice(PALETTE)), "color": rng.choice(PALETTE)}, "bannerImage": None, "isAdult": False, "format": "TV", "type": "ANIME", "status": "FINISHED", "episodes": 12, "chapters": None, "meanScore": 70, "averageScore": 70, "popularity": 1200, "startDate": {"year": 2015, "month": 4, "day": 1}}
    rel = [{"relationType": rng.choice(["SEQUEL", "PREQUEL", "SIDE_STORY", "SPIN_OFF"]), "node": ref(j)} for j in pick(rng.randint(0, 3))]
    recs = [{"rating": rng.randint(1, 90), "mediaRecommendation": {k: v for k, v in ref(j).items() if k not in ("type", "chapters")} | {"genres": ["Drama"]}} for j in pick(rng.choice([4, 10, 25]))]
    payload = {
        "id": nid, "idMal": None, "title": {"romaji": romaji, "english": english, "native": f"{romaji}（仮）"},
        "description": f"{name} is a fictional demo title generated by AniServe's demo generator. It follows a small cast through {eps} episodes of invented adventures.",
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
    sd = payload["startDate"]
    row = {"id": nid, "payload": json.dumps(payload, ensure_ascii=False), "search_text": text, "status": status, "season_year": year, "season": season, "format": fmt, "popularity": pop,
           "trending": payload["trending"], "mean_score": score, "genres": genres, "tags": [t["name"] for t in tags],
           "next_airing_at": datetime.fromtimestamp(nxt["airingAt"], timezone.utc).isoformat() if nxt else None, "refresh_after": refresh.isoformat() if refresh else None,
           "indexed_at": now.isoformat(), "is_adult": adult, "favourites": payload["favourites"], "average_score": score, "media_updated_at": payload["updatedAt"],
           "start_key": sd["year"] * 10000 + sd["month"] * 100 + sd["day"]}
    return nid, payload, row, chars_total


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--count", type=int, default=180)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=str(ROOT / "data" / "demo.sqlite"))
    ap.add_argument("--jsonl", help="also write the release's JSON Lines files into this folder")
    a = ap.parse_args()
    src = (ROOT / "genres-tags.js").read_text(encoding="utf-8")
    start = src.index("const TAGS = ") + len("const TAGS = ")
    tags_pool = json.loads(src[start:src.index(";", start)])
    rng = random.Random(a.seed); now = datetime.now(timezone.utc)
    ids = [9_000_001 + i for i in range(a.count)]
    catalog, pages = [], []
    for i in range(a.count):
        nid, payload, row, chars_total = build(rng, i, ids, tags_pool, now)
        catalog.append(row)
        if chars_total > 25:  # a stored second page of characters, as a fuller dump would hold
            items = [{**payload["characters"]["edges"][0], "node": {**payload["characters"]["edges"][0]["node"], "id": 800_000 + nid % 1000 * 100 + k}} for k in range(min(25, chars_total - 25))]
            pages.append({"id": nid, "kind": "characters", "page": 2, "items": json.dumps(items, ensure_ascii=False), "total": chars_total, "fetched_at": now.isoformat()})
    state = [{"key": k, "value": '{"ok": true}', "updated_at": (now - timedelta(hours=random.Random(k).randint(1, 20))).isoformat()} for k in ("initial", "recent", "schedule", "rank_popular", "rank_trending", "links")]
    data = {"anilist_catalog": catalog, "anilist_detail_pages": pages, "anilist_index_state": state, "anilist_missing_ids": []}

    out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True); out.unlink(missing_ok=True)
    con = sqlite3.connect(out)
    ctype = {"int": "INTEGER", "bool": "INTEGER", "text": "TEXT", "json": "TEXT", "array": "TEXT", "time": "TEXT"}
    for name, cols in TABLES.items():
        con.execute(f'CREATE TABLE "{name}" ({", ".join(chr(34) + c + chr(34) + " " + ctype[k] for c, k in cols)})')
        con.executemany(f'INSERT INTO "{name}" VALUES ({",".join("?" * len(cols))})',
                        [[json.dumps(r[c], ensure_ascii=False) if k == "array" else (int(r[c]) if k == "bool" else r[c]) for c, k in cols] for r in data[name]])
    for sql in INDEXES: con.execute(sql)
    con.commit(); con.close()
    print(f"wrote {out}: {a.count} demo titles, {len(pages)} with a stored second page of characters")

    if a.jsonl:
        folder = Path(a.jsonl); folder.mkdir(parents=True, exist_ok=True)
        for name, cols in TABLES.items():
            with (folder / f"public.{name}.jsonl").open("w", encoding="utf-8") as f:
                for r in data[name]:
                    row = {c: (json.loads(r[c]) if k == "json" and r[c] is not None else r[c]) for c, k in cols}
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"wrote JSON Lines files to {folder}")


if __name__ == "__main__":
    main()
