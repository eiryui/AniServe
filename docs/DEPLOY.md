# Loading the dump into a database and connecting AniServe

AniServe reads the AniList database dump published at
<https://github.com/eiryui/AniList-DB-Dump/releases/latest> and serves it as read-only JSON.
It never writes to the database. This guide shows, for each database, which release file to use,
how to load it, and which `DATABASE_URL` to set.

**Status of each recipe.** "Tested" means I ran the exact commands against files produced by the dump's own
export script from generated demo data (the real release assets could not be downloaded in my sandbox).
Run `python tests/test_api.py` after setup to confirm your own install.

| Database | Release file | Convert first? | `DATABASE_URL` | Status |
|---|---|---|---|---|
| SQLite | `.sqlite` | no | `sqlite:///data/anilist.sqlite` | tested |
| DuckDB | `.duckdb` | no | `duckdb:///data/anilist.duckdb` | tested |
| Parquet | `-parquet.zip` (unzip it) | no | `parquet:///data/parquet` | tested |
| PostgreSQL | `.dump`, `.tar`, `.sql.gz` | no, restore it | `postgresql://user:pass@host:5432/db` | tested on a local server |
| PostgreSQL | `-csv.zip` | stage and insert | same | tested |
| SQLite / DuckDB | `-csv.zip`, `-tsv.zip`, `-json.zip` | `load_dump.py` | as above | tested |
| Hosted PostgreSQL (Supabase, Neon, Railway, Render) | same as PostgreSQL | no | add `?sslmode=require` | **untested** |
| MySQL / MariaDB / SQL Server / Mongo | n/a | n/a | not supported | n/a |

The release also lists several PostgreSQL formats. File names follow `anilist-db-dump-<tag>-<format>.<ext>`;
choose by extension, and check every download against `SHA256SUMS-<tag>.txt` first (below).

## 0. Download and verify

```bash
TAG=manual-2026-11-02-0543          # or the latest tag
gh release download "$TAG" -R eiryui/AniList-DB-Dump -p '*sqlite*' -p 'SHA256SUMS*' -p 'MANIFEST*'
sha256sum -c --ignore-missing SHA256SUMS-$TAG.txt
```

`MANIFEST-<tag>.json` lists the row counts (for that release: 18,118 titles in `anilist_catalog`). After loading,
`GET /stats` should report the same number of `entries`.

The dump has four tables: `anilist_catalog` (required), `anilist_detail_pages`, `anilist_index_state`,
`anilist_missing_ids`. Only the catalogue is required; the others improve paging of characters and
recommendations and the "last refreshed" timestamps.

## 1. SQLite (simplest, no server)

```bash
mkdir -p data && mv anilist-db-dump-*.sqlite data/anilist.sqlite
echo 'DATABASE_URL=sqlite:///data/anilist.sqlite' > .env
uvicorn app:app --port 8002
```

Three slashes then a relative path; **four slashes for an absolute path** (`sqlite:////srv/anilist.sqlite`).
AniServe opens the file read-only, so the file may sit on a read-only volume.

## 2. DuckDB

```bash
pip install duckdb
echo 'DATABASE_URL=duckdb:///data/anilist.duckdb' > .env
```

By default the data is copied into RAM at startup (about a second for 18k titles in my test; fastest queries).
Set `ANISERVE_DUCKDB_MODE=disk` to read the file in place instead: lower memory, roughly 5 to 10 times slower per query.
Memory use is about 2 to 3 times the payload size; I measured about 1 GB RSS for a synthetic 18,000-title set with a
457 MB payload, and the real dump is 153 MB, so expect a few hundred MB (not measured on the real file).

## 3. Parquet

```bash
unzip anilist-db-dump-*-parquet.zip -d data/parquet     # public.anilist_catalog.parquet, ...
echo 'DATABASE_URL=parquet:///data/parquet' > .env
```

Same `ANISERVE_DUCKDB_MODE` option as DuckDB. It works well when the files live on object storage mounted as a folder
and you do not want a database.

## 4. PostgreSQL

Create an empty database and restore into it. Use whichever file you downloaded:

```bash
createdb anilist

# .dump (pg_dump custom format) or .tar
pg_restore --no-owner --no-privileges -d anilist anilist-db-dump-*.dump

# .sql.gz (plain SQL)
gunzip -c anilist-db-dump-*.sql.gz | psql -v ON_ERROR_STOP=1 -d anilist

# .sql.zst / .sql.zip: decompress the same way, then pipe into psql
zstd -dc file.sql.zst | psql -v ON_ERROR_STOP=1 -d anilist
```

(`.sql.zst` and `.sql.zip` were not run; they use the same plain-SQL file as `.sql.gz`.)

Connect:

```
DATABASE_URL=postgresql://user:password@localhost:5432/anilist
```

If you restored into a schema other than `public` and it is not on your `search_path`, set `ANISERVE_SCHEMA=<schema>`.

### PostgreSQL from the CSV files

`-csv.zip` has no schema, so create the tables first. The release's `.sql.gz` already contains the schema;
extract just the `CREATE TABLE` statements, or restore with `pg_restore --schema-only`, then:

```bash
unzip anilist-db-dump-*-csv.zip -d csv
psql -d anilist -f schema.sql                      # tables only (pg_restore --schema-only -f schema.sql file.dump)
psql -d anilist <<'SQL'
CREATE TEMP TABLE stage (LIKE anilist_catalog);
\copy stage FROM 'csv/public.anilist_catalog.csv' CSV HEADER
-- is_adult, favourites, average_score, media_updated_at and start_key are generated columns: PostgreSQL computes them
INSERT INTO anilist_catalog (id, payload, search_text, status, season_year, season, format, popularity, trending, mean_score,
                             genres, tags, next_airing_at, refresh_after, indexed_at)
  SELECT id, payload, search_text, status, season_year, season, format, popularity, trending, mean_score,
         genres, tags, next_airing_at, refresh_after, indexed_at FROM stage;
\copy anilist_detail_pages FROM 'csv/public.anilist_detail_pages.csv' CSV HEADER
\copy anilist_index_state  FROM 'csv/public.anilist_index_state.csv'  CSV HEADER
\copy anilist_missing_ids  FROM 'csv/public.anilist_missing_ids.csv'  CSV HEADER
SQL
```

I tested this sequence against a CSV export. If a column list differs in a newer dump, run `\d anilist_catalog`
and adjust. Restoring a `.dump` or `.sql.gz` is simpler and is the recommended route.

### A read-only role (recommended on a shared server)

```sql
CREATE ROLE aniserve LOGIN PASSWORD 'change-me';
GRANT CONNECT ON DATABASE anilist TO aniserve;
GRANT USAGE ON SCHEMA public TO aniserve;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO aniserve;
```

AniServe also sets each transaction read-only itself, so even a role with write rights cannot be used to change data through it.
It sets this per transaction (not as a connection option), so it works behind PgBouncer and Supabase/Neon poolers.

### Hosted PostgreSQL

Create the database in the service's dashboard, restore with `pg_restore` or `psql` using the connection string the
service shows you, then use that string with `?sslmode=require` as `DATABASE_URL`. I could not test any hosted service from
my sandbox; the connection handling is plain `psycopg2`, so any server that accepts a normal PostgreSQL connection should work.

## 5. CSV, TSV and JSON Lines into SQLite or DuckDB

```bash
python load_dump.py anilist-db-dump-<tag>-csv.zip  --to sqlite --out data/anilist.sqlite
python load_dump.py anilist-db-dump-<tag>-tsv.zip  --to duckdb --out data/anilist.duckdb
python load_dump.py anilist-db-dump-<tag>-json.zip --to sqlite --out data/anilist.sqlite
python load_dump.py ./unzipped-folder              --to sqlite --out data/anilist.sqlite
```

It accepts the zip, a folder, or single files named `<schema>.<table>.csv|tsv|jsonl`, converts PostgreSQL arrays and timestamps,
and adds indexes. Empty CSV fields become NULL. Speed: SQLite is quick; DuckDB conversion took about 70 seconds for 18,000 titles
(row-by-row inserts), so prefer the release's own `.duckdb` or Parquet file if you want DuckDB.

## 6. Docker

```bash
docker build -t aniserve .
docker run -p 8002:8002 -v "$PWD/data:/app/data:ro" -e DATABASE_URL=sqlite:///data/anilist.sqlite aniserve
```

For PostgreSQL pass `-e DATABASE_URL=postgresql://...` (use the host's address, not `localhost`, from inside the container).
The Dockerfile installs all drivers. **I could not run Docker in my sandbox, so the image build and `docker run` are untested.**

## 7. Checking that it works

```bash
curl localhost:8002/health          # {"status":"online","database":"connected","engine":"sqlite",...}
curl localhost:8002/stats | head -c 300
python tests/test_api.py            # builds demo data and tests SQLite, DuckDB, Parquet (+ PostgreSQL with TEST_POSTGRES_URL)
```

If `/health` says `degraded`, its `problem` field says why (file not found, wrong URL scheme, missing table, missing driver).

## Keeping the data fresh

AniServe only reads. When a new release is published, download it, load it into a fresh database or file and
restart (SQLite/DuckDB/Parquet: swap the file; PostgreSQL: restore into a new database and change `DATABASE_URL`, or truncate and reload).
