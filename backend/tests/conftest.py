"""Shared pytest setup.

* Unit tests never read backend/.env (hermetic: no real Bedrock / 우리말샘 calls, no costs).
* Live tests are opt-in (deselected otherwise):
  - ``live_smoke``: the small real-call subset run by Stage 1 of the evaluation (``npm run test:all``). It loads
    backend/.env itself and FAILS (never skips) when AWS credentials / LLM config are missing.
  - ``bedrock_live`` / ``urimalsaem_live``: the full live sets (``npm run test:live``), skip without keys.
* PostgreSQL: ONE embedded server (pixeltable-pgserver, no system install) per test session, started lazily
  by the first test that needs it and stopped + deleted at session end (no leftover postgres processes).
  ``pg_uri`` is the server's default database; ``pg_db`` is a fresh database per test (dropped afterwards).
  If the server cannot start, the tests ERROR with the collected diagnostics (also written to
  reports/pg-startup-error.log) instead of being silently skipped; ``HANJA_PG_OPTIONAL=1`` turns that into
  a skip. ``TEST_DATABASE_URL`` uses an external server instead.
"""
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest

os.environ["HANJA_SKIP_DOTENV"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[2]
LIVE_MARKERS = ("live_smoke", "bedrock_live", "urimalsaem_live")
PG_ERROR_LOG = ROOT / "reports" / "pg-startup-error.log"


def pytest_configure(config):
    config.addinivalue_line("markers", "live_smoke: small real Bedrock/우리말샘 subset for Stage 1 (-m live_smoke)")
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


# ---- embedded PostgreSQL ------------------------------------------------------------------------
def _diagnostics(data_dir: Path) -> str:
    lines = [f"  TEMP={os.environ.get('TEMP')!r} TMP={os.environ.get('TMP')!r} "
             f"SystemRoot set={bool(os.environ.get('SystemRoot') or os.environ.get('SYSTEMROOT'))} "
             f"LOCALAPPDATA set={bool(os.environ.get('LOCALAPPDATA'))} cwd={os.getcwd()!r}"]
    log = data_dir / "log"
    if log.exists():
        tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-25:]
        lines.append("  postgres log tail:\n" + "\n".join("    " + x for x in tail))
    return "\n".join(lines)


def _start_embedded():
    """(server, uri) or raise RuntimeError with every attempt's error + diagnostics."""
    from db.pg import EmbeddedPostgresUnavailable, start_embedded_postgres_server

    errors = []
    candidates = [Path(tempfile.mkdtemp(prefix="hanja-pg-")) / "data",
                  ROOT / ".pgtest" / f"run-{os.getpid()}-{uuid.uuid4().hex[:6]}" / "data"]
    for attempt, data_dir in enumerate(candidates * 2):  # each location twice (slow first start / AV scan)
        if attempt >= len(candidates):
            data_dir = data_dir.parent / f"retry-{attempt}"
        try:
            srv = start_embedded_postgres_server(data_dir)
            return srv, srv.get_uri()
        except EmbeddedPostgresUnavailable as e:
            errors.append(f"{data_dir}: {e}\n{_diagnostics(data_dir)}")
            time.sleep(1 + attempt)
    PG_ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
    PG_ERROR_LOG.write_text("\n".join(errors) + "\n", encoding="utf-8")
    raise RuntimeError("embedded PostgreSQL could not start (details in reports/pg-startup-error.log):\n"
                       + "\n".join(errors))


@pytest.fixture(scope="session")
def pg_server():
    if os.environ.get("TEST_DATABASE_URL"):
        yield None, os.environ["TEST_DATABASE_URL"]
        return
    try:
        srv, uri = _start_embedded()
    except RuntimeError as e:
        if os.environ.get("HANJA_PG_OPTIONAL") == "1":
            pytest.skip(str(e))
        pytest.fail(str(e), pytrace=False)
    PG_ERROR_LOG.unlink(missing_ok=True)
    yield srv, uri
    from db.pg import shutdown_embedded_postgres

    shutdown_embedded_postgres(srv)  # stop + delete data dir, kill any postgres left on it


@pytest.fixture(scope="session")
def pg_uri(pg_server):
    """URI of the session's PostgreSQL server (default database)."""
    return pg_server[1]


@pytest.fixture
def pg_db(pg_server):
    """A fresh, empty database for one test (dropped afterwards, open connections included)."""
    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    base = pg_server[1]
    name = "t_" + uuid.uuid4().hex[:12]
    with psycopg.connect(base, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}"')
    params = conninfo_to_dict(base)
    params["dbname"] = name
    yield make_conninfo(**params)
    with psycopg.connect(base, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
