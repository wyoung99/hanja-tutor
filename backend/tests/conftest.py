"""Shared pytest setup.

* Unit tests never read backend/.env (hermetic: no real Bedrock / 우리말샘 calls, no costs).
* Live tests are opt-in: ``bedrock_live`` / ``urimalsaem_live`` run only when selected with
  ``-m bedrock_live`` / ``-m urimalsaem_live`` (npm --prefix frontend run test:live) and then skip with
  "AWS credentials not configured" (or "OPENDICT_API_KEY not configured") when keys are missing.
* ``pg_uri`` fixture: a project-local embedded PostgreSQL (pixeltable-pgserver, no system install).
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

os.environ["HANJA_SKIP_DOTENV"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

LIVE_MARKERS = ("bedrock_live", "urimalsaem_live")


def pytest_configure(config):
    config.addinivalue_line("markers", "bedrock_live: real AWS Bedrock calls (opt-in: -m bedrock_live)")
    config.addinivalue_line("markers", "urimalsaem_live: real 우리말샘 API calls (opt-in: -m urimalsaem_live)")
    config.addinivalue_line("markers", "postgres: uses the embedded PostgreSQL server")


def pytest_collection_modifyitems(config, items):
    expr = config.getoption("markexpr") or ""
    for item in items:
        for m in LIVE_MARKERS:
            if m in item.keywords and m not in expr:
                item.add_marker(pytest.mark.skip(reason=f"opt-in live test: run with -m {m}"))


@pytest.fixture(scope="session")
def pg_uri():
    """URI of an embedded PostgreSQL started in a temp dir (deleted at the end of the session)."""
    if os.environ.get("TEST_DATABASE_URL"):
        yield os.environ["TEST_DATABASE_URL"]
        return
    from db.pg import EmbeddedPostgresUnavailable, start_embedded_postgres

    try:
        uri = start_embedded_postgres(Path(tempfile.mkdtemp(prefix="hanja-pg-")) / "data", cleanup="delete")
    except EmbeddedPostgresUnavailable as e:
        pytest.skip(f"embedded PostgreSQL could not start: {e}")
    yield uri
