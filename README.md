<div align="center">

<img src="ui/assets/logo.png" alt="AniServe" width="240" height="240">

# AniServe

**Your AniList database, served as a REST API.**

[![Version](https://img.shields.io/badge/version-1.6.3-6fe3ff?style=for-the-badge)](#)
[![Python](https://img.shields.io/badge/python-3.11%2B-8f82ff?style=for-the-badge&logo=python&logoColor=white)](#quickstart)
[![FastAPI](https://img.shields.io/badge/FastAPI-async-7df0b8?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-read--only-2a6cff?style=for-the-badge&logo=postgresql&logoColor=white)](#the-database)
[![License: MIT](https://img.shields.io/badge/license-MIT-ffd45e?style=for-the-badge)](LICENSE)

[Quickstart](#quickstart) · [Docker](#docker) · [Configuration](#configuration) · [The database](#the-database) · [Endpoints](#endpoint-reference) · [Testing](#testing) · [Troubleshooting](#troubleshooting)

**Made by [AziF](https://github.com/az4if)**

</div>

---

## What it is

AniServe is a **PostgreSQL → REST/JSON** service. It reads AniList records that are already stored in a PostgreSQL database and serves them over the same routes, with the same query logic and the same response shapes. Point your frontend's `API_BASE` at it and nothing else changes.

```text
PostgreSQL (anilist_catalog, ...)  ──▶  AniServe  ──▶  your frontend
        ▲
        └── restored from the AniList database dump (see "Get the database" below)
```

- **Same output.** Routes and response shapes match the Eiryui and Miruro API, so existing frontends keep working.
- **Read-only.** Every database session is opened read-only, so AniServe cannot create, change or delete a row. A read replica or a read-only role is enough.
- **No crawler.** It never calls AniList: no GraphQL client, no request budget, no background jobs, no advisory lock. You can run as many copies as you like.
- **Nothing to migrate.** It reads the tables from the database dump as they are. No schema changes.

AniServe does **not** fill the database, it only reads it. Restore a dump first, as described next.

| Feature | Details |
|---|---|
| **Drop-in metadata API** | `/search`, `/filter`, `/multi-search`, `/trending`, `/popular`, `/recent`, `/upcoming`, `/spotlight`, `/schedule`, `/info/:id`, `/anime/:id/*`, `/suggestions`, `/genres`, `/tags`, `/list`. Response shapes match the Eiryui and Miruro API routes. |
| **Per-IP rate limit** | Fixed-window limit with JSON `429` responses and `X-RateLimit-*` headers. |
| **Compression** | Brotli with a gzip fallback. |
| **Resilient startup** | If the database is not reachable yet, AniServe keeps retrying in the background instead of serving `503` until you restart it. |
| **Docs built in** | A landing page with a live poster shelf and an endpoint reference, plus Swagger UI and ReDoc. |

---

## Get the database

The latest AniList database dump is published as a GitHub release, rebuilt automatically every Saturday (00:00 UTC):

**<https://github.com/eiryui/AniList-DB-Dump/releases/latest>**

Each release comes in four formats:

| File | Use it with | For AniServe |
|---|---|---|
| `.dump` | `pg_restore` (PostgreSQL custom format) | **yes**, the smallest and fastest |
| `.sql` | `psql` (plain SQL) | **yes** |
| `.duckdb` | DuckDB | no, for analysis in DuckDB |
| `.sqlite` | SQLite | no, for analysis in SQLite |

AniServe reads PostgreSQL only, so restore the `.dump` or the `.sql` into any PostgreSQL database, local or remote:

```sh
# .dump
pg_restore --no-owner -d "$DATABASE_URL" anilist-db-dump-<date>.dump

# .sql
psql "$DATABASE_URL" -f anilist-db-dump-<date>.sql
```

Then set `DATABASE_URL` to that same database and start AniServe. To refresh the data, restore the newest dump the same way after each Saturday release.

---

## Quickstart

You need Python 3.11 or newer and a PostgreSQL database that holds the AniList tables.

```sh
cp .env.example .env
# set DATABASE_URL
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8001
```

Then open <http://localhost:8001>.

| Route | What you get |
|---|---|
| [`/`](http://localhost:8001/) | Landing page: live status, a poster shelf from `/trending`, the endpoint reference with copy-ready requests, and setup. Light and dark themes. |
| [`/docs`](http://localhost:8001/docs) · `/swagger` | Swagger UI, generated from the code. |
| [`/redoc`](http://localhost:8001/redoc) | ReDoc. |
| [`/openapi.json`](http://localhost:8001/openapi.json) | Raw OpenAPI schema. |
| [`/health`](http://localhost:8001/health) | Service and database status. |
| [`/license`](http://localhost:8001/license) | The MIT license text. |

---

## Docker

```sh
docker build -t aniserve .
docker run -d --name aniserve \
  -p 8001:8001 \
  --env-file .env \
  aniserve
```

- The container honours `$PORT` (default `8001`).
- `DATABASE_URL` must be reachable from inside the container. `localhost` there means the container itself; use `host.docker.internal` (Docker Desktop) or the database container's name on a shared network.
- `.dockerignore` keeps `.env` out of the image. Pass settings with `--env-file` at run time.

---

## Configuration

Copy [`.env.example`](.env.example) to `.env`. Every variable is optional except `DATABASE_URL`.

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | none | **Required.** PostgreSQL connection string. Without it every data route returns `503`. |
| `ALLOWED_ORIGINS` | `*` | Comma-separated CORS origins. |
| `PORT` | `8001` | Read by the Docker `CMD` only. With plain `uvicorn`, pass `--port`. |
| `APP_ENVIRONMENT` | `production` | `production` or `development`. Development adds a `debug` block to `/health` and writes errors to `logs/error.txt`. **Do not expose it publicly.** |
| `DATABASE_POOL_SIZE` | `10` | Maximum PostgreSQL connections held by the API (minimum 2). Keep it below your database plan's connection limit. |
| `THREAD_POOL_SIZE` | `32` | Worker threads for blocking database work. |
| `API_RATE_LIMIT_RPM` | `120` | Requests per minute for each client IP. `0` disables the limit. |
| `API_RATE_LIMIT_WINDOW_SECONDS` | `60` | Fixed bucket length. 120 RPM with a 10 s window allows 20 requests per window. |
| `API_RATE_LIMIT_TRUST_PROXY` | `false` | Use `X-Forwarded-For` for the client IP. Enable **only** behind a trusted reverse proxy, otherwise clients can spoof it. |
| `API_STRICT_MIRURO_SHAPE` | `true` | `true`: every route returns exactly Miruro's fields. `false`: keep the richer stored fields (cover colours, `isAdult` on related titles, `updatedAt`, ...). |

Requests under `/assets`, `/favicon.ico` and CORS `OPTIONS` preflights are not counted against the rate limit.

---

## The database

AniServe reads three tables. [`schema.sql`](schema.sql) holds their definition (PostgreSQL 12 or newer, for the generated columns); restoring the dump gives you these tables.

| Table | Needed | Holds | Read by |
|---|---|---|---|
| `anilist_catalog` | yes | One row per title: the full record as `payload` JSONB, plus the columns used to filter and sort (`status`, `season_year`, `season`, `format`, `popularity`, `trending`, `genres`, `tags`, `next_airing_at`, generated `is_adult`, `favourites`, `average_score`, `media_updated_at`, `start_key`). | every data route |
| `anilist_detail_pages` | no | Characters and recommendations past the first 25, one row per block of 25. | `/anime/{id}/characters`, `/anime/{id}/recommendations` |
| `anilist_index_state` | no | Job timestamps. | `/health` in development mode |

If `anilist_detail_pages` does not exist, AniServe serves only the 25 items stored on each record. If `anilist_catalog` does not exist, data routes answer `503` with a message saying so.

To prepare an empty database (for tests, or to load data from another source): `psql "$DATABASE_URL" -f schema.sql`.

### Characters and recommendations: blocks of 25

AniList serves characters and recommendations 25 at a time, and they are stored the same way: block 1 sits on the title's record in `anilist_catalog`, blocks 2, 3, ... are rows in `anilist_detail_pages`. AniServe cuts the window a request asks for out of those blocks:

1. `/anime/{id}/characters` and `/anime/{id}/recommendations` cut the requested window from the blocks. The client's `per_page` does not matter: items 21 to 30 use blocks 1 and 2, items 51 to 100 use blocks 3 and 4. With `per_page` left out the window is the whole list, so every stored item comes back in one response.
2. A block that is not in the database is not served. The response holds the items stored so far (possibly none), `total` is still AniList's real total, and `hasNextPage` is only true when the next item exists.
3. Every one of these routes, and `/info`, sets an **`X-Index-Status`** header: `complete` when everything for that title is stored, `partial` otherwise.
4. `/info` always returns `staff`, `externalLinks`, `streamingEpisodes` and `stats`; for a title that is not fully stored yet they are empty (and `X-Index-Status` is `partial`), so clients can read them without checks.

Other things AniServe does on the way out, object keys are put back into one fixed, readable order (PostgreSQL's JSONB reorders them), `timeUntilAiring` is recomputed from `airingAt` on every response, missing cover colours on related titles are filled in from the catalog, and adult titles are removed from relations and recommendations unless `isAdult=true`.

---

## Endpoint reference

All routes are `GET`. Collections take `page` (default `1`, minimum 1) and `per_page` (default `20`, range 1 to 50; anything outside returns `422`). The three detail routes (`characters`, `recommendations`, `relations`) are the exception: they have no default `per_page` and return everything when it is left out.

**Adult content.** Every data route takes `isAdult` (all except `/health`), including `/list`. With `isAdult=true`, adult titles are included alongside everything else. With `isAdult=false` or no parameter, adult titles are hidden: they are left out of lists and search, `/info/{id}` and `/anime/{id}/...` return 404 for them, adult entries are removed from `relations` and `recommendations`, and `/genres` leaves out the Hentai genre. `/tags` is a fixed list and is the same either way. Relations and recommendations are matched by the adult flag AniList returns for them, or by the catalog when the title is indexed.

### Search and filter

| Route | Parameters | Notes |
|---|---|---|
| `/search` | `query` (required), `page`, `per_page`, `isAdult` | Case-insensitive match on romaji, English and native titles and synonyms. Every word must appear, in any order. |
| `/suggestions` | `query` (required), `isAdult` | Up to 8 results with `id`, `title`, `title_romaji`, `poster`, `format`, `status`, `year`, `episodes`. With `API_STRICT_MIRURO_SHAPE=false` each result also carries `coverImage`. |
| `/filter` | `q`, `genre`, `tag`, `year`, `season`, `format`, `status`, `sort`, `page`, `per_page`, `isAdult` | All filters are optional and combine with AND. |
| `/multi-search` | same as `/filter` | Identical to `/filter` except `per_page` defaults to `30`. |

`/filter` details:

- `genre` and `tag` can be repeated (`?genre=Action&genre=Comedy`). A title must carry **all** of the values given.
- `year` is AniList's season year.
- `season`: `WINTER`, `SPRING`, `SUMMER`, `FALL`.
- `format`: `TV`, `TV_SHORT`, `MOVIE`, `SPECIAL`, `OVA`, `ONA`, `MUSIC`.
- `status`: `FINISHED`, `RELEASING`, `NOT_YET_RELEASED`, `CANCELLED`, `HIATUS`.
- `season`, `format` and `status` are case-insensitive.
- `sort`: `NAME_DESC` is most first and `NAME_ASC` is least first, for `POPULARITY` (default `POPULARITY_DESC`), `SCORE` (average score), `TRENDING`, `START_DATE` (real start date), `FAVOURITES`, `UPDATED_AT` and `ID`. Missing values always sort last, ties fall back to popularity and then id, and an unknown value returns 400.

### Collections

| Route | Contents |
|---|---|
| `/trending` | Sorted by AniList's trending score. |
| `/popular` | Sorted by popularity. |
| `/recent` | Currently releasing titles, newest start date first. |
| `/upcoming` | Titles with status `NOT_YET_RELEASED`. |
| `/spotlight` | The 10 trending titles as `{ "results": [...] }`, with no pagination fields. |
| `/schedule` | Upcoming airings, soonest first, one entry per title for its next episode. See [response shapes](#response-shapes). |

### Detail pages

| Route | Contents |
|---|---|
| `/info/{id}` | The full record for an AniList id, including staff, external links, streaming episodes and score stats. `404` if it is not in the database. Staff, links, streaming episodes and stats are whatever is stored for the title; AniServe never calls AniList, and a title that is not fully stored returns them empty with `X-Index-Status: partial`. Voice actors are Japanese only here. |
| `/anime/{id}/characters` | Characters with voice actors (all languages). **Without `per_page` every character is returned** (`page` 1, `perPage` = `total`). With `per_page` (1 to 50) it pages normally with `page` (default 1). Stored in blocks of 25: the first block is on the catalog record, later blocks are in `anilist_detail_pages` and only served once stored (`X-Index-Status: partial` until then). See [Characters and recommendations](#characters-and-recommendations-blocks-of-25). |
| `/anime/{id}/relations` | Related media (sequels, prequels, adaptations and so on). All of them are returned unless you pass `per_page` (1 to 50, with `page`), which adds `page`, `perPage`, `total` and `hasNextPage` to the body. |
| `/anime/{id}/recommendations` | Recommendations with the real `total`. **Without `per_page` every recommendation is returned**: `/anime/21/recommendations` with a `total` of 100 returns all 100. With `per_page` (1 to 50) it pages normally with `page` (default 1). Same blocks of 25 and the same `X-Index-Status` header as characters. `/info` still lists the first 10. |

### Catalog and service

| Route | Contents |
|---|---|
| `/genres` | The fixed genre list from `genres-tags.js` as `{ "results": [{ "name", "isAdult" }] }`. `isAdult` is true for Hentai, which is listed only with `isAdult=true`. |
| `/tags` | The fixed tag list from `genres-tags.js`, same shape. `isAdult` is always `false`. |
| `/list` | All stored titles with full records, sorted by id. Adult titles only with `isAdult=true`. Takes `page`, `per_page` (max 500), `ids_only`. |
| `/health` | Status of the service and the database. |
| `/docs`, `/redoc`, `/swagger`, `/openapi.json` | Interactive API docs (Swagger UI at `/docs` and `/swagger`, ReDoc at `/redoc`) and the OpenAPI schema. |
| `/` | The landing page and endpoint reference. |
| `/favicon.ico`, `/logo.png`, `/license` | The site icon (also at `/logo.png`) and the MIT license text. |

---

## Response shapes

**Collections** (`/search`, `/filter`, `/multi-search`, `/trending`, `/popular`, `/recent`, `/upcoming`, `/schedule`):

```json
{
  "page": 1,
  "perPage": 20,
  "total": 1234,
  "hasNextPage": true,
  "results": [ { "id": 154587, "title": { "romaji": "…", "english": "…", "native": "…" }, "…": "…" } ]
}
```

Collection items use the slim list shape (no description, characters, relations or recommendations; main studios only). Every `coverImage` in every response, including those inside relations and recommendations, carries `large`, `color` and (where available) `extraLarge`. Use `/info/{id}` for the full record.

**`/schedule`** items include the Miruro list fields plus `next_episode`, `airingAt` (unix seconds) and `timeUntilAiring` (seconds), With `API_STRICT_MIRURO_SHAPE=false` (the default is `true`) each item also gets the flat fields `color` (cover accent colour), `banner` (banner image URL) and `synopsis` (description).

**Detail routes** return `characters`, `relations` or `recommendations` arrays inside their own envelopes. `/anime/{id}/relations` also returns the show's `id` and `title`.

**No `per_page`, no paging.** On `/anime/{id}/characters`, `/anime/{id}/recommendations` and `/anime/{id}/relations`, leaving `per_page` out returns the whole list at once. For characters and recommendations the envelope is `{ "page": 1, "perPage": <total>, "total": <total>, "hasNextPage": false, ... }`, and `page=2` or higher is empty because everything is on page 1. `total` is AniList's real total, so while a title is only partly stored the array can hold fewer items than `total` and the `X-Index-Status` header says `partial`; adult entries are left out unless `isAdult=true`, so the array can also be shorter than `total` for that reason.

---

## Rate limiting and errors

Each client IP gets `API_RATE_LIMIT_RPM` requests per window. Every response carries:

| Header | Meaning |
|---|---|
| `X-RateLimit-Limit` | Requests allowed per window. |
| `X-RateLimit-Remaining` | Requests left in the current window. |
| `X-RateLimit-Reset` | Seconds until the current window ends. |

An over-limit client receives `429` with `Retry-After`:

```json
{ "detail": "API rate limit exceeded. Try again after the requested window.", "retry_after": 23 }
```

| Status | Meaning |
|---|---|
| `400` | `/filter` and `/multi-search`: unknown `sort` value. |
| `404` | `Anime not found`: the id is not in the database (yet). |
| `422` | `page` or `per_page` out of range. |
| `429` | Client rate limit exceeded. |
| `503` | `catalog database unavailable`: `DATABASE_URL` is missing or the connection failed. |


---

## Testing

The tests run against a real PostgreSQL, because the routes are SQL. Use a scratch database: it is emptied and re-seeded (the seeder refuses databases whose name does not contain `test`).

```sh
pip install -r requirements-dev.txt
createdb aniserve_test
TEST_DATABASE_URL=postgresql://postgres@localhost:5432/aniserve_test pytest
```

Without `TEST_DATABASE_URL` the tests are skipped. [`tests/seed.py`](tests/seed.py) builds a deterministic fixture catalog (adult and regular titles, every status and format, airing schedules, relations and recommendations to known and unknown titles, partly indexed character and recommendation blocks).

---

## Project layout

```text
AniServe/
├── app.py                 # FastAPI app: routes, query logic, shaping, rate limiter (read-only)
├── schema.sql             # The tables AniServe reads
├── genres-tags.js         # Fixed genre and tag lists served by /genres and /tags
├── ui/
│   ├── landing.html       # Landing page (served at /)
│   └── assets/            # landing.css, landing.js, endpoints.js (endpoint reference data), icons, logo
├── tests/                 # pytest suite, conftest and the fixture seeder
├── Keepalive/             # Optional Cloudflare Worker that pings /health so a free-tier host stays awake
├── requirements.txt
├── requirements-dev.txt
├── Dockerfile
├── .env.example
├── LICENSE
└── README.md
```

### Adding or changing an endpoint

1. Add the route in `app.py`.
2. Add its entry to `ui/assets/endpoints.js`. The landing page's reference, examples and route count all read from there.
3. Update the [endpoint reference](#endpoint-reference) above if behaviour changed.

---

## Tech stack

| Tool | Role |
|---|---|
| [FastAPI](https://fastapi.tiangolo.com) and [Uvicorn](https://www.uvicorn.org) | Async web framework and server |
| [PostgreSQL](https://www.postgresql.org) with [psycopg2](https://www.psycopg.org) | JSONB storage, GIN indexes, read-only connection pool |
| [python-dotenv](https://github.com/theskumar/python-dotenv) | `.env` loading |
| [brotli-asgi](https://github.com/fullonic/brotli-asgi) | Brotli compression with gzip fallback |
| Vanilla HTML, CSS and JS | Landing page, with no build step and no framework |

---

## Troubleshooting

<details>
<summary><b>Data routes return <code>503 catalog database unavailable</code></b></summary>

`DATABASE_URL` is missing or the database cannot be reached. Set it in `.env`, restart, and check `/health` for `database: "connected"`. If the database was down when AniServe started it keeps retrying on its own. In Docker, `localhost` refers to the container, not your machine.

</details>

<details>
<summary><b>Data routes return <code>503 catalog tables missing</code></b></summary>

The database is reachable but has no `anilist_catalog` table. Restore the [AniList database dump](https://github.com/eiryui/AniList-DB-Dump/releases/latest) into it, or create the tables with `psql "$DATABASE_URL" -f schema.sql` and load your data.

</details>

<details>
<summary><b>Results are empty, or <code>/info/{id}</code> returns 404</b></summary>

The database does not hold that title (yet). AniServe serves whatever is stored, so an empty or half-filled database gives empty or partial answers. In development mode `/health` shows the catalog count and job timestamps.

</details>

<details>
<summary><b>Everyone gets <code>429</code> behind a reverse proxy</b></summary>

Without `API_RATE_LIMIT_TRUST_PROXY=true` every request appears to come from the proxy's IP and shares one bucket. Enable it only if the proxy sets `X-Forwarded-For` and clients cannot reach the app directly.

</details>

<details>
<summary><b>The landing page shows empty poster frames</b></summary>

`/trending` returned nothing or failed. Open it directly to see why: an empty list means the database has no titles, and `503` means the database is unavailable.

</details>

<details>
<summary><b>Fonts look different on the landing page</b></summary>

The page loads its fonts from Google Fonts. Offline or behind a blocker, it falls back to system fonts and still works.

</details>

---

## Credits

| Role | Who |
|---|---|
| Code and design | **[AziF](https://github.com/az4if)** |
| Database dump | [eiryui/AniList-DB-Dump](https://github.com/eiryui/AniList-DB-Dump) |
| Anime metadata | [AniList](https://anilist.co) |

---

## License

Released under the [MIT License](LICENSE). See the license file for the copyright notice.

---

## Disclaimer

This project is not affiliated with AniList. It serves metadata that was retrieved through the AniList API, and you are responsible for following AniList's terms of service and for how you use the data.
