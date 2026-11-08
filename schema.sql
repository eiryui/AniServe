-- Tables AniServe reads. AniList Indexer creates and fills them; AniServe itself never writes to them.
-- Use this file to prepare an empty database (for tests, or for loading data from another source):
--   psql "$DATABASE_URL" -f schema.sql
-- Requires PostgreSQL 12 or newer (generated columns).

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

-- Pages of a title's characters / recommendations beyond the first 25. One row = one AniList page of 25
-- (page 1 is items 1-25, page 2 items 26-50, ...). Optional: without it AniServe serves only the 25 items stored on each record.
CREATE TABLE IF NOT EXISTS anilist_detail_pages (id INTEGER NOT NULL, kind TEXT NOT NULL, page INTEGER NOT NULL, items JSONB NOT NULL, total INTEGER, fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (id, kind, page));

-- Written by the indexer for its own bookkeeping; AniServe only reads it for the development-mode /health diagnostics. Optional.
CREATE TABLE IF NOT EXISTS anilist_index_state (key TEXT PRIMARY KEY, value JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
