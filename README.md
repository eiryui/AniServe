# Kura

**Your AniList catalogue, shelved as read-only JSON.**

Kura is the *database to JSON* half of AniList Indexer. The indexer crawls AniList and fills PostgreSQL. Kura only reads that database and serves it as a fast JSON API, with an in-browser explorer and a one-command exporter. It never contacts AniList and never writes a row.

```text
AniList ──▶ AniList Indexer ──▶ PostgreSQL ──▶ Kura ──▶ your frontend / JSON files
             (writer, 1 only)     (JSONB)      (reader, as many as you like)
```

Why split them: the indexer needs one writer, a schedule and AniList's rate limit. The API needs none of that, so it can scale out, sit behind a CDN, run with a read-only database role, or be public while the indexer stays private.

## Quickstart

You need Python 3.11+ and a database an AniList Indexer (1.8 or newer) has already filled.

```sh
cp .env.example .env          # set DATABASE_URL
pip install -r requirements.txt
uvicorn app:app --port 8002
```

Open <http://localhost:8002>. The wall at the top is the live catalogue; the explorer below sends real requests and shows the response as titles, a record page, or highlighted JSON. Request state lives in the URL hash, so any view can be shared as a link.

No indexer yet? Try it on a throwaway database with invented titles:

```sh
python tools/seed_demo.py --count 180   # the only script that writes
```

## Database to JSON

| How | Command |
|---|---|
| HTTP, streamed | `curl -o catalog.ndjson http://localhost:8002/export` (add `?format=json` for one array) |
| No server | `python export.py catalog.ndjson` |
| One array | `python export.py catalog.json --format json` |
| One file per title | `python export.py shelf/ --format files` writes `shelf/9000/9000003.json` (folders of 1000 ids) |

Records come out in the same shape as `/list`: the full stored record, fixed key order, bookkeeping keys removed, adult titles left out unless you pass `isAdult=true` (HTTP) or `--adult` (CLI). Add `--pretty` for indented files.

## Routes

All `GET`. Collections take `page`, `per_page` (1 to 50) and `isAdult`, and return `{ page, perPage, total, hasNextPage, results }`.

| Route | Notes |
|---|---|
| `/search?query=` | Every word must appear in a title or synonym, any order. |
| `/filter`, `/multi-search` | `q`, `genre`, `tag` (repeatable), `year`, `season`, `format`, `status`, `sort`. |
| `/suggestions?query=` | Up to 8 results for autocomplete. |
| `/popular` `/trending` `/recent` `/upcoming` `/spotlight` `/schedule` | Collections. `/schedule` adds `next_episode`, `airingAt`, `timeUntilAiring` (recomputed on every request). |
| `/info/{id}` | Full stored record. |
| `/anime/{id}/characters`, `/relations`, `/recommendations` | Detail pages. |
| `/genres`, `/tags`, `/list` | Fixed lists, and every record by id (up to 500 per page). |
| **`/stats`** | New. Counts by status, format, decade and genre, plus when the indexer last ran each job. |
| **`/export`** | New. Streams the whole catalogue. |
| `/health`, `/docs`, `/redoc`, `/swagger`, `/openapi.json` | Status and API docs. |

Sort names are `POPULARITY`, `SCORE`, `TRENDING`, `START_DATE`, `FAVOURITES`, `UPDATED_AT` and `ID`, each with `_DESC` (most first) or `_ASC`.

### What differs from the indexer's own API

The routes and response shapes are the same, so a frontend can switch over by changing its base URL. Three behaviours differ because Kura cannot reach AniList:

- `/info/{id}` includes `staff`, `externalLinks`, `streamingEpisodes` and `stats` only if the indexer's API already fetched and stored them for that title. Kura never fetches them.
- `/anime/{id}/characters` and `/recommendations` serve the first 25 plus any later blocks the indexer's API has stored. A page past the stored data comes back empty, and `total` still reports AniList's real count. Pages are never stitched across a gap.
- Kura adds `/stats`, `/export` and an `X-Kura-Ms` timing header, and turns CORS credentials off.

If you need those fetch-on-demand details, keep using the indexer's API for them, or let a title be opened there once.

## Safety

- Every database session is opened with `default_transaction_read_only=on` and a 30 s statement timeout. Even a bug cannot modify the catalogue, and a read-only Postgres role works.
- Kura checks that `anilist_catalog` and its generated columns exist, reports the problem in `/health` if not, and reconnects by itself when the database or the indexer's schema appears later.
- Dead pooled connections are detected and replaced. If the database is unreachable you get `503`, not a stack trace.
- One full export runs at a time so it cannot starve the API. Set `KURA_ENABLE_EXPORT=false` to disable `/export` on a public deployment.

## Configuration

See [`.env.example`](.env.example). Only `DATABASE_URL` is required.

## Docker

```sh
docker build -t kura .
docker run -p 8002:8002 -e DATABASE_URL=postgresql://user:pass@host/db kura
```

`localhost` inside the container is the container itself; use `host.docker.internal` or a network name for a local database.

## Tests

```sh
pip install -r requirements-dev.txt
DATABASE_URL=postgresql://... python tests/test_api.py
```

39 checks against a seeded database: response shapes, sorting and paging, search escaping, adult hiding, stored-block paging, fresh `timeUntilAiring`, export, read-only enforcement, connection recovery and the rate limiter.

## Name ideas

Kura is a working title. It is short, easy to type, and fits the idea: a *kura* (蔵) is the fireproof storehouse where a household keeps what it values, and the UI borrows its famous white-plaster and diamond-tile wall. Alternatives:

| Name | Meaning | Feel |
|---|---|---|
| **Kura** 蔵 | Storehouse | Short, calm, the current pick. |
| **Tana** 棚 | Shelf | Friendly; "your catalogue on a shelf". |
| **Zaiko** 在庫 | Stock in a warehouse | Playful, inventory-minded. |
| **Shikkui** 漆喰 | The white plaster of a kura wall | Distinctive but hard to type. |
| **Kawara** 瓦 | Roof tile | Pairs well with the tile motif. |
| **Mado** 窓 | Window | "A read-only window onto the database". |
| **Shelfline** | English: shelf plus pipeline | Plain, searchable. |
| **Readroom** | English: a reading room | Says "read-only" directly. |

To rename: change `APP_NAME` in `app.py`, the wordmark and `<title>` in `ui/index.html`, and the heading in this file.

## Credits and licence

Derived from AniList Indexer by JsmaAtom (MIT), whose record shaping and route contract Kura keeps. UI by this project. MIT licensed, see [LICENSE](LICENSE). Not affiliated with AniList; anime data belongs to its respective owners and to AniList's terms.
