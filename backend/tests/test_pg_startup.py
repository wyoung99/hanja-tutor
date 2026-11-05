"""Embedded PostgreSQL start-up robustness.

Found failure mode: on Windows, a process whose environment lacks SystemRoot (stripped/sandboxed runners) cannot
initialise a cluster -- initdb's bootstrap PANICs "could not generate secret authorization token"
(0xC0000409). db.pg.ensure_windows_env() restores SystemRoot/windir before the server starts.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from db.pg import ensure_windows_env

BACKEND = Path(__file__).resolve().parents[1]
CHILD = """
import sys
sys.path.insert(0, {backend!r})
from db.pg import start_embedded_postgres_server, shutdown_embedded_postgres
s = start_embedded_postgres_server({data!r})
try:
    print("READY" if s.get_postmaster_info().status == "ready" else "NOT READY")
finally:
    shutdown_embedded_postgres(s)
"""


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only environment variables")
def test_missing_systemroot_is_restored(monkeypatch):
    monkeypatch.delenv("SystemRoot", raising=False)
    monkeypatch.delenv("windir", raising=False)
    assert set(ensure_windows_env()) == {"SystemRoot", "windir"}
    assert Path(os.environ["SystemRoot"], "System32").is_dir()
    assert ensure_windows_env() == []


@pytest.mark.postgres
@pytest.mark.skipif(sys.platform != "win32", reason="the failure mode is Windows-specific")
def test_embedded_server_starts_from_a_stripped_environment(tmp_path):
    child = CHILD.format(backend=str(BACKEND), data=str(tmp_path / "data"))
    env = {"PATH": os.environ["PATH"]}  # no SystemRoot, TEMP, LOCALAPPDATA, ...: initdb + start must still work
    r = subprocess.run([sys.executable, "-c", child], env=env, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=240, stdin=subprocess.DEVNULL)
    assert r.returncode == 0 and r.stdout.strip().endswith("READY"), r.stdout[-1500:] + r.stderr[-1500:]
