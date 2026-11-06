"""Durable application state: one PostgreSQL table per entity, written as a unit of work.

The services keep their working set in memory (fast, and the in-memory implementation is what the unit
tests exercise). With a database configured, ``StateSync`` makes PostgreSQL the system of record:

* at startup every registered collection is loaded from its table (``restore``), so a restarted server
  -- or a second ``create_app`` on the same database -- continues with the same accounts and learning state;
* ``flush()`` takes a snapshot of every collection, diffs it against what was last written and applies all
  inserts/updates/deletes in ONE transaction. The app calls it before each HTTP response is sent (and after
  streamed responses / background jobs), so an acknowledged request is durable;
* deleting an entity in memory deletes its row; ``delete_child_rows`` additionally removes every row that
  carries the child's id, and ``rows_for_child`` counts what is left (AC3 checks).

Every table has the same shape: ``key TEXT PRIMARY KEY, child_id TEXT, parent_id TEXT, doc JSONB``
(+ ``updated_at``), with indexes on child_id/parent_id. Schema creation is idempotent (CREATE ... IF NOT
EXISTS) and versioned in ``app_schema_version``. Usage counters are NOT handled here: they need atomic
conditional increments and live in ``usage.PostgresCounterStore`` (``usage_counters``).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

log = logging.getLogger("hanja.state")
SCHEMA_VERSION = 2  # 2: id/password accounts (child_sessions, parent_password_failures; parent_pins dropped)

# (key, child_id, parent_id, doc)
Row = Tuple[str, Optional[str], Optional[str], dict]

# Every persisted collection. payments and payment_archive are separate tables on purpose (AC3: payment
# records of deleted accounts move to the separate 5-year retention table).
TABLES: Tuple[str, ...] = (
    "parents", "children", "dormant_parents", "dormant_children", "parent_sessions", "child_sessions",
    "study_plans", "review_items", "tracing_progress", "tracing_attempts", "mock_exams",
    "report_days", "review_answers", "weekly_summaries_sent",
    "screentime_settings", "screentime_days", "parent_password_failures", "extension_requests",
    "safety_events", "safety_thread_counts", "chat_locks",
    "paid_usage_events", "usage_limit_notices",
    "subscriptions", "payments", "payment_archive", "refund_requests",
    "dormant_vault", "notifications", "push_subscriptions",
)
# Rows of these tables are kept when a child is deleted (legal retention) -- none of them carry child_id.
RETAINED = ("payment_archive",)


class StateBackend:
    """In-memory default: nothing to load, nothing to write."""

    persistent = False

    def load(self, table: str) -> List[Row]:
        return []

    def apply(self, upserts: List[Tuple[str, Row]], deletes: List[Tuple[str, str]]) -> None:
        return None

    def rows_for_child(self, child_id: str) -> Dict[str, int]:
        return {}

    def delete_child_rows(self, child_id: str) -> None:
        return None

    def close(self) -> None:
        return None


class PostgresStateBackend(StateBackend):
    persistent = True

    def __init__(self, conn_string: str, tables: Sequence[str] = TABLES) -> None:
        from psycopg_pool import ConnectionPool

        self.tables = tuple(tables)
        self.pool = ConnectionPool(conn_string, min_size=1, max_size=4, open=True)
        self._init_schema()

    def _init_schema(self) -> None:
        with self.pool.connection() as c:
            c.execute("SELECT pg_advisory_xact_lock(724501)")  # concurrent create_app on one DB
            c.execute("CREATE TABLE IF NOT EXISTS app_schema_version (version INTEGER PRIMARY KEY,"
                      " applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
            for t in self.tables:
                c.execute(f"CREATE TABLE IF NOT EXISTS {t} (key TEXT PRIMARY KEY, child_id TEXT, parent_id TEXT,"
                          " doc JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now())")
                c.execute(f"CREATE INDEX IF NOT EXISTS {t}_child ON {t} (child_id)")
                c.execute(f"CREATE INDEX IF NOT EXISTS {t}_parent ON {t} (parent_id)")
            # v2 (2026-11-06): the parent PIN was replaced by the parent's account password -> obsolete PIN hashes go
            c.execute("DROP TABLE IF EXISTS parent_pins")
            c.execute("INSERT INTO app_schema_version (version) VALUES (%s) ON CONFLICT DO NOTHING",
                      (SCHEMA_VERSION,))

    def load(self, table: str) -> List[Row]:
        with self.pool.connection() as c:
            return [(k, ch, p, d) for k, ch, p, d in
                    c.execute(f"SELECT key, child_id, parent_id, doc FROM {table} ORDER BY updated_at, key")]

    def apply(self, upserts, deletes) -> None:
        from psycopg.types.json import Jsonb

        if not upserts and not deletes:
            return
        with self.pool.connection() as c:  # one transaction (commit on exit)
            with c.cursor() as cur:
                for table, key in deletes:
                    cur.execute(f"DELETE FROM {table} WHERE key = %s", (key,))
                for table, (key, child_id, parent_id, doc) in upserts:
                    cur.execute(f"INSERT INTO {table} (key, child_id, parent_id, doc) VALUES (%s, %s, %s, %s)"
                                " ON CONFLICT (key) DO UPDATE SET child_id = EXCLUDED.child_id,"
                                " parent_id = EXCLUDED.parent_id, doc = EXCLUDED.doc, updated_at = now()",
                                (key, child_id, parent_id, Jsonb(doc)))

    def rows_for_child(self, child_id: str) -> Dict[str, int]:
        out: Dict[str, int] = {}
        with self.pool.connection() as c:
            for t in self.tables:
                n = c.execute(f"SELECT count(*) FROM {t} WHERE child_id = %s", (child_id,)).fetchone()[0]
                if n:
                    out[t] = n
        return out

    def delete_child_rows(self, child_id: str) -> None:
        with self.pool.connection() as c:
            for t in self.tables:
                if t not in RETAINED:
                    c.execute(f"DELETE FROM {t} WHERE child_id = %s", (child_id,))

    def count(self, table: str, where: str = "TRUE", args: tuple = ()) -> int:
        with self.pool.connection() as c:
            return c.execute(f"SELECT count(*) FROM {table} WHERE {where}", args).fetchone()[0]

    def close(self) -> None:
        self.pool.close()


@dataclass
class Collection:
    table: str
    snapshot: Callable[[], Iterable[Row]]
    restore: Callable[[List[Row]], None]


def _dump(doc: dict) -> str:
    return json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class StateSync:
    def __init__(self, backend: Optional[StateBackend] = None) -> None:
        self.backend = backend or StateBackend()
        self.collections: List[Collection] = []
        self._last: Dict[str, Dict[str, str]] = {}
        self._lock = threading.RLock()

    @property
    def persistent(self) -> bool:
        return self.backend.persistent

    def register(self, table: str, snapshot: Callable[[], Iterable[Row]], restore: Callable[[List[Row]], None]):
        if self.backend.persistent and table not in getattr(self.backend, "tables", TABLES):
            raise ValueError(f"unknown state table {table}")
        self.collections.append(Collection(table, snapshot, restore))

    def load(self) -> Dict[str, int]:
        """Restore every collection from the database (startup). Returns rows per table."""
        out = {}
        with self._lock:
            for col in self.collections:
                rows = self.backend.load(col.table)
                if rows:
                    col.restore(rows)
                self._last[col.table] = {k: _dump(d) for k, _, _, d in rows}
                out[col.table] = len(rows)
        return out

    @staticmethod
    def _snapshot(col: Collection) -> List[Row]:
        # Request threads may mutate the dicts while we iterate; retry on "changed size during iteration".
        for attempt in range(20):
            try:
                return list(col.snapshot())
            except RuntimeError:
                time.sleep(0.005 * (attempt + 1))
        return list(col.snapshot())

    def flush(self) -> int:
        """Write every change since the last flush in one transaction. Returns the number of changed rows."""
        if not self.backend.persistent:
            return 0
        with self._lock:
            upserts: List[Tuple[str, Row]] = []
            deletes: List[Tuple[str, str]] = []
            new_last: Dict[str, Dict[str, str]] = {}
            for col in self.collections:
                prev = self._last.get(col.table, {})
                cur: Dict[str, str] = {}
                for key, child_id, parent_id, doc in self._snapshot(col):
                    s = _dump(doc)
                    cur[key] = s
                    if prev.get(key) != s:
                        upserts.append((col.table, (key, child_id, parent_id, doc)))
                deletes += [(col.table, k) for k in prev if k not in cur]
                new_last[col.table] = cur
            self.backend.apply(upserts, deletes)
            self._last.update(new_last)
            return len(upserts) + len(deletes)

    def rows_for_child(self, child_id: str) -> Dict[str, int]:
        return self.backend.rows_for_child(child_id)

    def close(self) -> None:
        self.backend.close()
