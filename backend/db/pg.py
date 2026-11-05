"""PostgreSQL access: an external DATABASE_URL or a project-local embedded server.

The embedded server is ``pixeltable-pgserver`` (pip wheel bundling PostgreSQL binaries); it needs no
system install or admin rights. Data lives in the directory passed in (e.g. backend/.pgdata).
"""
from __future__ import annotations

from pathlib import Path
from typing import Mapping, Optional

_SERVERS: dict = {}


class EmbeddedPostgresUnavailable(RuntimeError):
    pass


def start_embedded_postgres(data_dir: Path, cleanup: Optional[str] = "stop") -> str:
    """Start (or reuse) the embedded server for data_dir and return its connection URI."""
    data_dir = Path(data_dir).resolve()
    if data_dir in _SERVERS:
        return _SERVERS[data_dir].get_uri()
    try:
        import pixeltable_pgserver as pgs
    except ImportError as e:  # pragma: no cover - dependency missing
        raise EmbeddedPostgresUnavailable(f"pixeltable-pgserver not installed ({e})") from e
    data_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        srv = pgs.get_server(data_dir, cleanup_mode=cleanup)
    except Exception as e:  # noqa: BLE001
        raise EmbeddedPostgresUnavailable(f"{type(e).__name__}: {e}") from e
    _SERVERS[data_dir] = srv
    return srv.get_uri()


def start_embedded_postgres_server(data_dir: Path):
    """Tests: a throw-away server (cleanup_mode="delete"); returns the pgserver handle."""
    data_dir = Path(data_dir).resolve()
    try:
        import pixeltable_pgserver as pgs
    except ImportError as e:  # pragma: no cover
        raise EmbeddedPostgresUnavailable(f"pixeltable-pgserver not installed ({e})") from e
    data_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        return pgs.get_server(data_dir, cleanup_mode="delete")
    except Exception as e:  # noqa: BLE001
        raise EmbeddedPostgresUnavailable(f"{type(e).__name__}: {e}") from e


def postgres_processes(data_dir: Path) -> list:
    """Running postgres processes whose command line names data_dir (leftover check)."""
    import psutil

    from pixeltable_pgserver.utils import POSTGRES_PROCESS_NAMES, cmdline_names_data_dir

    out = []
    for proc in psutil.process_iter(attrs=("name", "cmdline")):
        try:
            if ((proc.info["name"] or "").lower() in POSTGRES_PROCESS_NAMES and proc.info["cmdline"]
                    and cmdline_names_data_dir(proc.info["cmdline"], Path(data_dir))):
                out.append(proc)
        except Exception:  # noqa: BLE001 - process vanished / access denied
            continue
    return out


def shutdown_embedded_postgres(srv) -> None:
    """Stop the server, delete its data dir and make sure no postgres process is left on it."""
    import shutil

    data_dir = Path(srv.pgdata)
    try:
        srv.cleanup()
    except Exception:  # noqa: BLE001
        pass
    for proc in postgres_processes(data_dir):
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
    shutil.rmtree(data_dir, ignore_errors=True)


def database_url(env: Mapping[str, str]) -> Optional[str]:
    """DATABASE_URL, else an embedded server when PG_EMBEDDED_DIR is set, else None (in-memory)."""
    if env.get("DATABASE_URL"):
        return env["DATABASE_URL"]
    if env.get("PG_EMBEDDED_DIR"):
        d = Path(env["PG_EMBEDDED_DIR"])
        if not d.is_absolute():
            d = Path(__file__).resolve().parents[1] / d
        return start_embedded_postgres(d)
    return None


def stop_embedded_postgres(data_dir: Path) -> bool:
    """Stop an embedded server left running for data_dir (e.g. after uvicorn was killed). True if stopped."""
    data_dir = Path(data_dir).resolve()
    if not (data_dir / "postmaster.pid").exists():
        return False
    from pixeltable_pgserver.pgexec import pgexec
    from pixeltable_pgserver.utils import POSTGRES_VERSIONS, TARGET_POSTGRES_VERSION

    pgexec("pg_ctl", ("-w", "-D", str(data_dir), "stop", "-m", "fast"),
           bin_path=POSTGRES_VERSIONS[TARGET_POSTGRES_VERSION], user=None)
    return True


def reset_embedded_postgres(data_dir: Path) -> None:
    """Stop a server left on data_dir and delete the directory (fresh database for a local E2E run)."""
    import shutil

    data_dir = Path(data_dir).resolve()
    try:
        stop_embedded_postgres(data_dir)
    except Exception:  # noqa: BLE001 - stale pid file
        pass
    for proc in postgres_processes(data_dir):
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
    shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":  # python -m db.pg stop|reset <dir>
    import sys

    if len(sys.argv) == 3 and sys.argv[1] == "stop":
        print("stopped" if stop_embedded_postgres(Path(sys.argv[2])) else "not running")
    elif len(sys.argv) == 3 and sys.argv[1] == "reset":
        reset_embedded_postgres(Path(sys.argv[2]))
        print("reset")
