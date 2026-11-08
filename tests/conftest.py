"""Tests run against a real PostgreSQL, because the routes are SQL.

  TEST_DATABASE_URL=postgresql://postgres@localhost:5432/aniserve_test pytest

The database is emptied and re-seeded with tests/seed.py, so point it at a scratch database (its name must contain "test"), never at real data. Without
TEST_DATABASE_URL (or when it cannot be reached) the tests are skipped.
"""
import importlib, os, sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(Path(__file__).resolve().parent))

DSN = os.getenv("TEST_DATABASE_URL", "")
_conn = None


def _connect():
    """One shared connection to the scratch database; every fixture skips (instead of failing) when there is none."""
    global _conn
    if not DSN:
        pytest.skip("set TEST_DATABASE_URL to a scratch PostgreSQL database to run the AniServe tests")
    if _conn is None or _conn.closed:
        import psycopg2
        try:
            _conn = psycopg2.connect(DSN, connect_timeout=5)
        except Exception as exc:
            pytest.skip(f"TEST_DATABASE_URL is not reachable: {exc}")
    return _conn


@pytest.fixture(scope="session")
def ids():
    import seed as seed_module
    return seed_module.seed(_connect())


@pytest.fixture(scope="session")
def aniserve(ids):
    os.environ.update(DATABASE_URL=DSN, API_RATE_LIMIT_RPM="0", API_STRICT_MIRURO_SHAPE="true", APP_ENVIRONMENT="production")
    sys.modules.pop("app", None)
    return importlib.import_module("app")


@pytest.fixture(scope="session")
def client(aniserve):
    from fastapi.testclient import TestClient
    with TestClient(aniserve.app) as c:
        c.headers["Accept-Encoding"] = "identity"
        yield c


@pytest.fixture(scope="session")
def adult_ids(ids):
    conn = _connect()
    with conn.cursor() as c:
        c.execute("SELECT id FROM anilist_catalog WHERE is_adult ORDER BY id"); r = [x[0] for x in c.fetchall()]
    conn.commit(); return r
