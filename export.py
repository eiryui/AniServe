"""Write the catalogue to JSON files without running the web server.

  python export.py catalog.ndjson                 one record per line (default)
  python export.py catalog.json --format json     a single JSON array
  python export.py shelf/ --format files          one <id>.json per title, in folders of 1000 ids

Reads DATABASE_URL (environment or .env), or pass --db. Works with PostgreSQL, SQLite, DuckDB and Parquet.
The data is opened read-only. Adult titles are left out unless --adult is given.
"""
import argparse, json, sys, time
from pathlib import Path

import app as aniserve
from stores import Catalog


def batches(adult, size=500):
    """Keyset pagination over the catalogue: stable and cheap on the primary key."""
    last = 0
    while True:
        items = aniserve.store().after(last, size, adult)
        if not items:
            return
        last = items[-1]["id"]
        if not adult: aniserve.hide_adult_links(items)
        aniserve.fill_colors(items)
        yield [aniserve.ordered(aniserve.fresh_airing(aniserve.clean(x))) for x in items]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", help="output file, or output directory for --format files")
    ap.add_argument("--format", choices=("ndjson", "json", "files"), default="ndjson")
    ap.add_argument("--db", help="database URL or file path (default: DATABASE_URL)")
    ap.add_argument("--adult", action="store_true", help="include adult titles")
    ap.add_argument("--pretty", action="store_true", help="indent output (json and files formats)")
    a = ap.parse_args()

    if a.db: aniserve.catalog = Catalog(a.db)
    aniserve.catalog.connect()
    if not aniserve.catalog.ok:
        sys.exit(f"error: {aniserve.catalog.problem}")

    dump = lambda x: json.dumps(x, ensure_ascii=False, indent=2 if a.pretty else None, separators=None if a.pretty else (",", ":"), default=str)
    out, n, t0 = Path(a.out), 0, time.time()
    if a.format == "files":
        out.mkdir(parents=True, exist_ok=True)
        for batch in batches(a.adult):
            for x in batch:
                folder = out / f"{x['id'] // 1000:04d}"; folder.mkdir(exist_ok=True)
                (folder / f"{x['id']}.json").write_text(dump(x), encoding="utf-8"); n += 1
            print(f"\r{n} titles", end="", file=sys.stderr)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as f:
            if a.format == "json": f.write("[")
            for batch in batches(a.adult):
                for x in batch:
                    f.write(("," if a.format == "json" and n else "") + dump(x) + ("\n" if a.format == "ndjson" else "")); n += 1
                print(f"\r{n} titles", end="", file=sys.stderr)
            if a.format == "json": f.write("]")
    print(f"\rwrote {n} titles to {out} in {time.time() - t0:.1f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
