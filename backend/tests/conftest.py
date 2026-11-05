"""Shared pytest setup.

* Unit tests never read backend/.env (hermetic: no real Bedrock / 우리말샘 calls, no costs).
* Live tests are opt-in (deselected otherwise): ``bedrock_live`` / ``urimalsaem_live`` run only when selected with
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
    """Live tests are deselected (not run, not counted as skips) unless their marker is selected."""
    expr = config.getoption("markexpr") or ""
    keep, drop = [], []
    for item in items:
        live = [m for m in LIVE_MARKERS if m in item.keywords]
        (drop if live and not any(m in expr for m in live) else keep).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep


@pytest.fixture(scope="session")
def pg_uri():
    """URI of an embedded PostgreSQL started in a temp dir (deleted at the end of the session)."""
    if os.environ.get("TEST_DATABASE_URL"):
        yield os.environ["TEST_DATABASE_URL"]
        return
    from db.pg import EmbeddedPostgresUnavailable, start_embedded_postgres

    # First try a temp dir; if that fails (seen once only inside the Ouroboros evaluator, cause unknown),
    # retry in a short repo-local dir and record every error so the next run shows the real reason.
    errors = []
    candidates = [Path(tempfile.mkdtemp(prefix="hanja-pg-")) / "data",
                  Path(__file__).resolve().parents[2] / ".pgtest" / f"run-{os.getpid()}" / "data"]
    for data_dir in candidates:
        try:
            uri = start_embedded_postgres(data_dir, cleanup="delete")
            break
        except EmbeddedPostgresUnavailable as e:
            errors.append(f"{data_dir}: {e}")
    else:
        log = Path(__file__).resolve().parents[2] / "reports" / "pg-startup-error.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("
".join(errors) + "
", encoding="utf-8")
        pytest.skip("embedded PostgreSQL could not start: " + " | ".join(errors))
    yield uri
