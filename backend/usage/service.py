"""Usage limits per child profile.

* chatbot: daily (resets 00:00 KST); mock exam: weekly (resets Monday 00:00 KST).
* Limit values come from configuration (dict / env), never from code.
* Raising a limit applies immediately; lowering applies from the next reset. Past usage is
  never rewritten.
* `consume` is atomic, so two devices racing for the last unit cannot exceed the limit. Counters live in a
  CounterStore: in memory (one lock) for unit tests, or PostgreSQL (``usage_counters`` table, one
  conditional ``INSERT .. ON CONFLICT DO UPDATE .. WHERE used < limit RETURNING`` per request) in the app.
* Only the metered features are ever locked; everything in UNMETERED is never gated.
* Child-facing text never contains a subscription prompt; parents get it via notifier/view.
* Paid-state comes from a SubscriptionStatus interface (default: free).
"""
from __future__ import annotations

import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Mapping, Optional, Protocol, Tuple

KST = timezone(timedelta(hours=9))
CHATBOT, MOCKEXAM = "chatbot", "mockexam"
DAILY, WEEKLY = "daily", "weekly"
PERIOD = {CHATBOT: DAILY, MOCKEXAM: WEEKLY}
DEFAULT_LIMITS = {CHATBOT: 20, MOCKEXAM: 1}  # beta defaults
ENV_KEYS = {CHATBOT: "USAGE_CHATBOT_DAILY_LIMIT", MOCKEXAM: "USAGE_MOCKEXAM_WEEKLY_LIMIT"}
# Always free and unlimited; no counter and no lock ever applies to these.
UNMETERED = frozenset({"todays_hanja", "review", "tracing", "basic_report", "safety",
                       "conversation_view", "usage_time_limit", "harmful_input_alert"})


class FeatureLocked(Exception):
    pass


class LimitReached(FeatureLocked):
    def __init__(self, feature: str, resets_at: datetime) -> None:
        super().__init__(f"{feature} limit reached")
        self.feature, self.resets_at = feature, resets_at


class SubscriptionStatus(Protocol):
    def is_paid(self, child_id: str) -> bool: ...


class FreePlan:
    """Default subscription lookup: everyone is on the free plan."""

    def is_paid(self, child_id: str) -> bool:
        return False


FREE_PLAN = FreePlan()


def period_start(feature: str, now: datetime) -> datetime:
    k = now.astimezone(KST)
    day = k.replace(hour=0, minute=0, second=0, microsecond=0)
    if PERIOD[feature] == WEEKLY:
        day -= timedelta(days=day.weekday())  # Monday 00:00 KST
    return day


def next_reset(feature: str, now: datetime) -> datetime:
    start = period_start(feature, now)
    return start + (timedelta(days=7) if PERIOD[feature] == WEEKLY else timedelta(days=1))


def child_notice(feature: str) -> str:
    """Child screen: friendly notice only — no subscription / payment wording."""
    if feature == MOCKEXAM:
        return "이번 주 모의고사는 여기까지예요. 다음 주 월요일에 또 만나요!"
    return "오늘은 여기까지 할까요? 내일 또 만나요!"


def parent_notice(feature: str, child_nickname: str, resets_at: datetime) -> str:
    """Parent screen / push: includes the subscription prompt."""
    name = "챗봇 질문" if feature == CHATBOT else "모의고사"
    return (f"{child_nickname}의 {name} 무료 한도에 도달했어요. {resets_at.astimezone(KST):%m/%d %H:%M}에 초기화돼요. "
            "구독하면 무제한으로 이용할 수 있어요.")


@dataclass
class UsageLimitConfig:
    """Limit values + change semantics (raise: immediately, lower: next reset)."""
    limits: Dict[str, int] = field(default_factory=lambda: dict(DEFAULT_LIMITS))
    _pending: Dict[str, Tuple[int, datetime]] = field(default_factory=dict)  # feature -> (value, effective_from)

    @classmethod
    def from_mapping(cls, m: Optional[Mapping[str, int]] = None) -> "UsageLimitConfig":
        lim = dict(DEFAULT_LIMITS)
        for k, v in (m or {}).items():
            if k not in PERIOD or int(v) < 0:
                raise ValueError(f"bad limit {k}={v}")
            lim[k] = int(v)
        return cls(lim)

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "UsageLimitConfig":
        env = os.environ if env is None else env
        return cls.from_mapping({f: int(env[k]) for f, k in ENV_KEYS.items() if k in env})

    def effective(self, feature: str, now: datetime) -> int:
        pend = self._pending.get(feature)
        if pend and now >= pend[1]:
            self.limits[feature] = pend[0]
            del self._pending[feature]
        return self.limits[feature]

    def set_limit(self, feature: str, value: int, now: datetime) -> None:
        if feature not in PERIOD or value < 0:
            raise ValueError("bad limit")
        cur = self.effective(feature, now)
        if value >= cur:  # raise: immediate (and cancels a pending cut)
            self.limits[feature] = value
            self._pending.pop(feature, None)
        else:  # lower: from the next reset
            self._pending[feature] = (value, next_reset(feature, now))


class CounterStore(Protocol):
    """(child, feature, period_start) -> used. ``try_take`` must be atomic."""

    def get(self, child_id: str, feature: str, start: datetime) -> int: ...

    def try_take(self, child_id: str, feature: str, start: datetime, limit: Optional[int]) -> Optional[int]:
        """Add one unit unless used >= limit (limit None = no cap). Returns the count before, or None."""

    def give_back(self, child_id: str, feature: str, start: datetime) -> None: ...
    def reset(self, child_id: str, feature: str, start: datetime) -> None: ...
    def delete_child(self, child_id: str) -> None: ...
    def rows_for_child(self, child_id: str) -> int: ...


class InMemoryCounterStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: Dict[Tuple[str, str, datetime], int] = {}

    def get(self, child_id, feature, start) -> int:
        with self._lock:
            return self._counts.get((child_id, feature, start), 0)

    def try_take(self, child_id, feature, start, limit):
        with self._lock:
            key = (child_id, feature, start)
            used = self._counts.get(key, 0)
            if limit is not None and used >= limit:
                return None
            self._counts[key] = used + 1
            return used

    def give_back(self, child_id, feature, start) -> None:
        with self._lock:
            key = (child_id, feature, start)
            if self._counts.get(key, 0) > 0:
                self._counts[key] -= 1

    def reset(self, child_id, feature, start) -> None:
        with self._lock:
            self._counts.pop((child_id, feature, start), None)

    def delete_child(self, child_id: str) -> None:
        with self._lock:
            for k in [k for k in self._counts if k[0] == child_id]:
                del self._counts[k]

    def rows_for_child(self, child_id: str) -> int:
        with self._lock:
            return sum(1 for k in self._counts if k[0] == child_id)


class PostgresCounterStore:
    """``usage_counters`` table. Every increment is one conditional upsert (row lock), so concurrent requests
    from several devices -- or several server processes -- can never pass the limit."""

    DDL = ("CREATE TABLE IF NOT EXISTS usage_counters ("
           " child_id TEXT NOT NULL, feature TEXT NOT NULL, period_start TIMESTAMPTZ NOT NULL,"
           " used INTEGER NOT NULL CHECK (used >= 0), PRIMARY KEY (child_id, feature, period_start))")

    def __init__(self, conn_string: str, max_size: int = 8) -> None:
        from psycopg_pool import ConnectionPool

        self.pool = ConnectionPool(conn_string, min_size=1, max_size=max_size, kwargs={"autocommit": True},
                                   open=True)
        with self.pool.connection() as c:
            c.execute(self.DDL)

    def _one(self, sql: str, args: tuple):
        with self.pool.connection() as c:
            return c.execute(sql, args).fetchone()

    def get(self, child_id, feature, start) -> int:
        r = self._one("SELECT used FROM usage_counters WHERE child_id=%s AND feature=%s AND period_start=%s",
                      (child_id, feature, start))
        return r[0] if r else 0

    def try_take(self, child_id, feature, start, limit):
        if limit is not None and limit <= 0:
            return None
        cap = 2**31 - 1 if limit is None else int(limit)
        r = self._one("INSERT INTO usage_counters (child_id, feature, period_start, used) VALUES (%s, %s, %s, 1)"
                      " ON CONFLICT (child_id, feature, period_start) DO UPDATE SET used = usage_counters.used + 1"
                      " WHERE usage_counters.used < %s RETURNING used", (child_id, feature, start, cap))
        return None if r is None else r[0] - 1

    def give_back(self, child_id, feature, start) -> None:
        self._one("UPDATE usage_counters SET used = used - 1 WHERE child_id=%s AND feature=%s AND period_start=%s"
                  " AND used > 0 RETURNING used", (child_id, feature, start))

    def reset(self, child_id, feature, start) -> None:
        self._one("DELETE FROM usage_counters WHERE child_id=%s AND feature=%s AND period_start=%s RETURNING 1",
                  (child_id, feature, start))

    def delete_child(self, child_id: str) -> None:
        self._one("DELETE FROM usage_counters WHERE child_id=%s RETURNING 1", (child_id,))

    def rows_for_child(self, child_id: str) -> int:
        return self._one("SELECT count(*) FROM usage_counters WHERE child_id=%s", (child_id,))[0]

    def close(self) -> None:
        self.pool.close()


@dataclass
class Reservation:
    id: str
    child_id: str
    feature: str
    period: datetime
    paid_event: bool = False
    released: bool = False


@dataclass
class PaidUsageEvent:
    reservation_id: str
    child_id: str
    feature: str
    at: datetime


class UsageService:
    def __init__(
        self,
        config: Optional[UsageLimitConfig] = None,
        clock: Callable[[], datetime] = lambda: datetime.now(KST),
        subscription: SubscriptionStatus = FREE_PLAN,
        parent_of: Optional[Callable[[str], Tuple[str, str]]] = None,  # child_id -> (parent_id, nickname)
        notifier=None,
        counters: Optional[CounterStore] = None,
    ) -> None:
        self.config = config or UsageLimitConfig()
        self.clock, self.subscription = clock, subscription
        self.parent_of, self.notifier = parent_of, notifier
        self._lock = threading.Lock()  # paid events / notified set / limit config
        self.counters: CounterStore = counters or InMemoryCounterStore()
        self.paid_events: List[PaidUsageEvent] = []  # evidence for later refund judgement
        self._notified: set = set()

    # ---- reads ---------------------------------------------------------
    def _count(self, child_id: str, feature: str, now: datetime) -> int:
        return self.counters.get(child_id, feature, period_start(feature, now))

    def _limit(self, feature: str, now: datetime) -> int:
        with self._lock:
            return self.config.effective(feature, now)

    def limit(self, feature: str) -> int:
        return self._limit(feature, self.clock())

    def is_locked(self, child_id: str, feature: str) -> bool:
        if feature in UNMETERED or self.subscription.is_paid(child_id):
            return False
        now = self.clock()
        return self._count(child_id, feature, now) >= self._limit(feature, now)

    def snapshot(self, child_id: str, feature: str) -> dict:
        now = self.clock()
        lim = self._limit(feature, now)
        used = self._count(child_id, feature, now)
        paid = self.subscription.is_paid(child_id)
        return {"feature": feature, "used": used, "limit": None if paid else lim,
                "locked": (not paid) and used >= lim, "resets_at": next_reset(feature, now)}

    # ---- writes --------------------------------------------------------
    def consume(self, child_id: str, feature: str) -> Reservation:
        """Atomically take one unit or raise LimitReached. Unmetered features never count."""
        if feature in UNMETERED:
            raise ValueError(f"{feature} is not metered")
        paid = self.subscription.is_paid(child_id)
        now = self.clock()
        start = period_start(feature, now)
        lim = self._limit(feature, now)
        before = self.counters.try_take(child_id, feature, start, None if paid else lim)  # atomic
        if before is None:
            resets = next_reset(feature, now)
            key = (child_id, feature, start)
            with self._lock:
                first = key not in self._notified
                self._notified.add(key)
            if first:
                self._push_parent(child_id, feature, resets)
            raise LimitReached(feature, resets)
        res = Reservation(uuid.uuid4().hex, child_id, feature, start)
        if paid and before >= lim:  # beyond the free limit while subscribed
            res.paid_event = True
            with self._lock:
                self.paid_events.append(PaidUsageEvent(res.id, child_id, feature, now))
        return res

    def release(self, res: Reservation) -> None:
        """Give the unit back (e.g. failed chatbot response). Idempotent."""
        with self._lock:
            if res.released:
                return
            res.released = True
            if res.paid_event:
                self.paid_events = [e for e in self.paid_events if e.reservation_id != res.id]
        self.counters.give_back(res.child_id, res.feature, res.period)

    def reset_counters(self, child_id: str) -> None:
        """Paid -> free transition: zero the current day/week counters of this child."""
        now = self.clock()
        for feature in PERIOD:
            self.counters.reset(child_id, feature, period_start(feature, now))
        with self._lock:
            self._notified = {k for k in self._notified if k[0] != child_id}

    def purge_child(self, child_id: str) -> None:
        """Profile/account deletion: counters, paid-use events and limit notices of this child."""
        self.counters.delete_child(child_id)
        with self._lock:
            self.paid_events = [e for e in self.paid_events if e.child_id != child_id]
            self._notified = {k for k in self._notified if k[0] != child_id}

    def record_paid_event(self, child_id: str, feature: str) -> PaidUsageEvent:
        """Record a paid-feature use that is not metered (e.g. detailed report view)."""
        with self._lock:
            ev = PaidUsageEvent(uuid.uuid4().hex, child_id, feature, self.clock())
            self.paid_events.append(ev)
        return ev

    def exam_charge(self) -> Callable[[str], Reservation]:
        """Hook for MockExamService(charge=...): called once per newly started exam."""
        return lambda child_id: self.consume(child_id, MOCKEXAM)

    def _push_parent(self, child_id: str, feature: str, resets_at: datetime) -> None:
        if not (self.notifier and self.parent_of):
            return
        parent_id, nick = self.parent_of(child_id)
        self.notifier.notify(parent_id, "usage_limit_reached", {
            "child_id": child_id, "feature": feature, "resets_at": resets_at.isoformat(),
            "message": parent_notice(feature, nick, resets_at), "subscribe_prompt": True})
