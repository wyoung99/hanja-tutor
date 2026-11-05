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
