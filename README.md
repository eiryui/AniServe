# AniServe

Serve the AniList database dump as a read-only JSON API, with a small browser UI.

Point AniServe at a database loaded from the dump
(<https://github.com/eiryui/AniList-DB-Dump/releases/latest>) and it serves the anime catalogue as JSON:
search, filters, trending, schedule, detail pages, characters, relations and recommendations.
It never writes to the database and never calls AniList.

```bash
pip install -r requirements.txt
cp .env.example .env            # set DATABASE_URL
uvicorn app:app --port 8002     # UI at http://localhost:8002/  API docs at /swagger
```

Want to try it before downloading the dump? `python tools/make_demo.py` writes `data/demo.sqlite` with invented titles;
then `DATABASE_URL=sqlite:///data/demo.sqlite`.

## Databases

| `DATABASE_URL` | Release file |
|---|---|
| `postgresql://user:pass@host:5432/db` | `.dump`, `.tar`, `.sql.gz` (restore), or CSV |
| `sqlite:///data/anilist.sqlite` | `.sqlite` |
| `duckdb:///data/anilist.duckdb` | `.duckdb` |
| `parquet:///data/parquet` | `-parquet.zip`, unzipped |
| any of the SQLite or DuckDB forms | CSV, TSV or JSON Lines, converted with `python load_dump.py` |

**[docs/DEPLOY.md](docs/DEPLOY.md)** has the step-by-step guide: verifying downloads, restoring into each database,
read-only roles, hosted PostgreSQL, Docker. MySQL, MariaDB, SQL Server and MongoDB are not supported.

For a minimal SQLite-only install: `pip install fastapi uvicorn`. The PostgreSQL and DuckDB drivers are imported only
when needed; a missing one shows up in `/health` as a clear message.

## Endpoints

`/search /multi-search /filter /suggestions /trending /popular /recent /upcoming /spotlight /genres /tags /schedule /list
/info/{id} /anime/{id}/characters /anime/{id}/relations /anime/{id}/recommendations /health /stats /export`

Adult titles are hidden unless `isAdult=true`. Lists accept `page` and `per_page` (max 50), and `/filter` accepts
`sort=POPULARITY_DESC|SCORE_DESC|TRENDING_DESC|START_DATE_DESC|FAVOURITES_DESC|UPDATED_AT_DESC|ID_ASC` (and `_ASC` variants).
Every response carries `X-AniServe-Ms`. `/swagger` lists all parameters.

`/stats` summarises the data (counts by status, format, decade, top genres, last refresh). `/export` streams the catalogue as
NDJSON (`?format=json` for one array, `?isAdult=true` to include adult titles); `python export.py OUT` does the same from the
command line. Set `ANISERVE_ENABLE_EXPORT=false` on a public deployment to switch the HTTP route off.

## JSON shape and what was checked

The JSON follows the Miruro/Eiryui-style API layout: same fields, same key order, same envelopes.
I verified this by running the reference implementation and AniServe side by side on the same data and comparing responses byte for byte:
200 requests (every list route, every sort in both directions, filters, search edge cases, adult handling, detail
pages, paged characters and recommendations, error responses), identical on PostgreSQL, SQLite, DuckDB, Parquet, and on
CSV, TSV and JSON converted to SQLite and DuckDB, in strict and non-strict shape mode (`timeUntilAiring` was compared after
removing it, since it depends on the clock).

Limits of that check, stated plainly:
- It used test data made with the dump's own export script, not the real release files, which I could not download.
- It covered only routes that do not need live AniList. `/health`, `/stats` and `/export` are new and have no original to compare with.

Intentional differences: AniServe never calls AniList, so a detail page contains only what the dump stores and
character/recommendation pages beyond the stored ones are empty rather than fetched; plus the new routes and the timing header.

## Performance

Measured on 18,000 generated titles (the real dump is 153 MB, similar in row count):

| Engine | Popular list | Text search | Filter | Notes |
|---|---|---|---|---|
| SQLite | ~20 ms | ~290 ms | ~340 ms | no startup cost |
| DuckDB, memory (default) | 14-19 ms | not recorded | not recorded | ~1.2 s startup, high RAM |
| DuckDB, disk | ~150 ms | not recorded | not recorded | low RAM |
| Parquet, memory / disk | 13-21 ms / 255-318 ms | not recorded | not recorded | |

PostgreSQL was verified for correctness, not benchmarked. `/export` of 17,102 rows took 30 to 45 seconds.

## Configuration

See `.env.example`; every variable is documented there (`DATABASE_URL`, `ANISERVE_SCHEMA`, `ANISERVE_DUCKDB_MODE`, `PORT`,
`ALLOWED_ORIGINS`, rate limits, `API_STRICT_MIRURO_SHAPE`, `ANISERVE_ENABLE_EXPORT`, `ANISERVE_STATEMENT_TIMEOUT_MS`).

## Tests

```bash
pip install -r requirements-dev.txt
python tests/test_api.py                                          # SQLite, DuckDB, Parquet (338 checks)
TEST_POSTGRES_URL=postgresql://... python tests/test_api.py       # plus PostgreSQL
```

## Safety

Each engine is opened read-only (PostgreSQL read-only transactions with a statement timeout; SQLite `mode=ro`;
DuckDB read-only or an in-memory copy), and the tests confirm the source files are byte-for-byte unchanged after a run.
There is a per-client rate limit. Behind a reverse proxy, set `API_RATE_LIMIT_TRUST_PROXY=true`.

## Licence

MIT, see `LICENSE`. Parts of this code derive from an MIT-licensed project, so its original copyright notice must stay.
AniServe is independent and not affiliated with AniList. Check the dump's and AniList's own terms before republishing the data.
