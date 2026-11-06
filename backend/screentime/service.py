"""Parental screen-time limits (KST): daily total, allowed time windows, 5-minute warning,
lock (deferred while a mock exam is in progress), extra time granted on the child's device with the
parent's account password, and push-approved extension requests.

Safety/supervision feature: never gated by subscription.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

from accounts.service import AccountService, NotFound, ValidationFailed

KST = timezone(timedelta(hours=9))
WARN_SECONDS = 300          # warn 5 minutes before the lock
MAX_HEARTBEAT_GAP = 120     # seconds counted per heartbeat at most (app closed != usage)
MAX_PARENT_PASSWORD_FAILURES = 5  # on the child's device; a parent login clears it
MAX_EXTRA_MINUTES = 240


class ScreenTimeError(Exception):
    code = "screentime_error"


class AppLocked(ScreenTimeError):
    code = "app_locked"

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class InvalidParentPassword(ScreenTimeError):
    code = "invalid_parent_password"


class ParentPasswordLocked(ScreenTimeError):
    code = "parent_password_locked"


class SettingsInvalid(ScreenTimeError):
    code = "settings_invalid"


@dataclass(frozen=True)
class TimeWindow:
    start_min: int  # minutes since KST midnight, inclusive
    end_min: int    # exclusive, 1..1440

    @staticmethod
    def parse(start: str, end: str) -> "TimeWindow":
        def m(s: str) -> int:
            h, mi = s.split(":")
            return int(h) * 60 + int(mi)
        try:
            w = TimeWindow(m(start), 1440 if end == "24:00" else m(end))
        except Exception as e:  # noqa: BLE001
            raise SettingsInvalid("time must be HH:MM") from e
        if not (0 <= w.start_min < w.end_min <= 1440):
            raise SettingsInvalid("window start must be before end within one day")
        return w


@dataclass
class ScreenTimeSettings:
    daily_minutes: Optional[int] = None          # None = no daily cap
    windows: List[TimeWindow] = field(default_factory=list)  # empty = all day allowed


@dataclass
class ExtensionRequest:
    id: str
    child_id: str
    minutes: int
    created_at: datetime
    status: str = "pending"  # pending | approved | rejected
    resolved_at: Optional[datetime] = None


@dataclass
class _Day:
    used_seconds: float = 0.0
    extra_seconds: int = 0
    override_until: Optional[datetime] = None
    last_beat: Optional[datetime] = None


class ScreenTimeService:
    def __init__(
        self,
        accounts: AccountService,
        clock: Callable[[], datetime] = lambda: datetime.now(KST),
        exam_in_progress: Callable[[str], bool] = lambda child_id: False,
        notify_parent: Optional[Callable[[str, str, dict], None]] = None,  # (parent_id, kind, payload)
    ) -> None:
        self.accounts, self.clock = accounts, clock
        self.exam_in_progress = exam_in_progress
        self.notify_parent = notify_parent or (lambda p, k, d: None)
        self.settings: Dict[str, ScreenTimeSettings] = {}
        self._days: Dict[tuple, _Day] = {}
        # wrong parent passwords typed on a child's device (extra time), per parent
        self._password_failures: Dict[str, int] = {}
        accounts.on_parent_login.append(lambda pid: self._password_failures.pop(pid, None))
        self.requests: Dict[str, ExtensionRequest] = {}

    # ---- helpers ---------------------------------------------------------
    def _now(self) -> datetime:
        return self.clock().astimezone(KST)

    def _day(self, child_id: str, d: Optional[date] = None) -> _Day:
        return self._days.setdefault((child_id, d or self._now().date()), _Day())

    def _owned(self, parent_id: str, child_id: str):
        return self.accounts._owned(parent_id, child_id)  # NotFound / Forbidden

    # ---- parent password (extra time on the child's device) ---------------
    def _check_parent_password(self, parent_id: str, password: str) -> None:
        if self._password_failures.get(parent_id, 0) >= MAX_PARENT_PASSWORD_FAILURES:
            raise ParentPasswordLocked("too many wrong parent passwords; log in to the parent account to unlock")
        if self.accounts.verify_parent_password(parent_id, password):
            self._password_failures.pop(parent_id, None)
            return
        self._password_failures[parent_id] = self._password_failures.get(parent_id, 0) + 1
        raise InvalidParentPassword("wrong parent password")

    # ---- settings --------------------------------------------------------
    def set_limits(self, parent_id: str, child_id: str, daily_minutes: Optional[int] = None,
                   windows: Optional[List[tuple]] = None) -> ScreenTimeSettings:
        self._owned(parent_id, child_id)
        if daily_minutes is not None and not (1 <= int(daily_minutes) <= 1440):
            raise SettingsInvalid("daily minutes must be 1..1440")
        ws = sorted((TimeWindow.parse(s, e) for s, e in (windows or [])), key=lambda w: w.start_min)
        for a, b in zip(ws, ws[1:]):
            if a.end_min > b.start_min:
                raise SettingsInvalid("windows overlap")
        st = ScreenTimeSettings(None if daily_minutes is None else int(daily_minutes), ws)
        self.settings[child_id] = st
        return st

    # ---- usage + status --------------------------------------------------
    def heartbeat(self, child_id: str) -> dict:
        """Called periodically while the app is open; accrues usage then returns status."""
        now = self._now()
        day = self._day(child_id)
        if day.last_beat is not None and day.last_beat.date() == now.date():
            gap = (now - day.last_beat).total_seconds()
            if gap > 0 and not self._status(child_id, now)["locked"]:
                day.used_seconds += min(gap, MAX_HEARTBEAT_GAP)
        day.last_beat = now
        return self.status(child_id)

    def status(self, child_id: str) -> dict:
        return self._status(child_id, self._now())

    def used_seconds(self, child_id: str, day: date) -> float:
        """Recorded app-use seconds for one KST day (reporting)."""
        d = self._days.get((child_id, day))
        return d.used_seconds if d else 0.0

    def _status(self, child_id: str, now: datetime) -> dict:
        if child_id not in self.accounts.children:
            raise NotFound("child")
        st = self.settings.get(child_id, ScreenTimeSettings())
        day = self._day(child_id, now.date())
        candidates: Dict[str, float] = {}
        if st.daily_minutes is not None:
            candidates["daily_limit"] = st.daily_minutes * 60 + day.extra_seconds - day.used_seconds
        if st.windows:
            mins = now.hour * 60 + now.minute + now.second / 60
            left = 0.0
            for w in st.windows:
                if w.start_min <= mins < w.end_min:
                    left = (w.end_min - mins) * 60
            if day.override_until and day.override_until > now:
                left = max(left, (day.override_until - now).total_seconds())
            candidates["outside_window"] = left
        if not candidates:
            return {"locked": False, "warning": False, "lock_pending": False, "reason": None,
                    "seconds_left": None}
        reason, left = min(candidates.items(), key=lambda kv: kv[1])
        left = max(0, int(left))
        in_exam = self.exam_in_progress(child_id)
        expired = left <= 0
        return {
            "locked": expired and not in_exam,
            "lock_pending": expired and in_exam,   # lock applies once the exam ends
            "warning": 0 < left <= WARN_SECONDS,
            "reason": reason if expired else None,
            "seconds_left": left,
        }

    def assert_can_use(self, child_id: str) -> None:
        s = self.status(child_id)
        if s["locked"]:
            raise AppLocked(s["reason"])

    def can_start_exam(self, child_id: str) -> bool:
        return not self.status(child_id)["locked"]

    # ---- extra time ------------------------------------------------------
    def _grant(self, child_id: str, minutes: int) -> None:
        if not (1 <= int(minutes) <= MAX_EXTRA_MINUTES):
            raise SettingsInvalid(f"extra minutes must be 1..{MAX_EXTRA_MINUTES}")
        now = self._now()
        day = self._day(child_id, now.date())
        day.extra_seconds += int(minutes) * 60
        base = day.override_until if day.override_until and day.override_until > now else now
        day.override_until = base + timedelta(minutes=int(minutes))

    def grant_extra_time(self, parent_id: str, child_id: str, parent_password: str, minutes: int) -> dict:
        self._owned(parent_id, child_id)
        self._check_parent_password(parent_id, parent_password)
        self._grant(child_id, minutes)
        return self.status(child_id)

    # ---- extension requests ---------------------------------------------
    def request_extension(self, child_id: str, minutes: int = 15) -> ExtensionRequest:
        child = self.accounts.children.get(child_id)
        if child is None:
            raise NotFound("child")
        if not (1 <= int(minutes) <= MAX_EXTRA_MINUTES):
            raise SettingsInvalid("invalid minutes")
        for r in self.requests.values():
            if r.child_id == child_id and r.status == "pending":
                return r  # one open request at a time
        req = ExtensionRequest(uuid.uuid4().hex, child_id, int(minutes), self._now())
        self.requests[req.id] = req
        self.notify_parent(child.parent_id, "extension_request",
                           {"request_id": req.id, "child_id": child_id,
                            "nickname": child.nickname, "minutes": req.minutes})
        return req

    def pending_requests(self, parent_id: str) -> List[ExtensionRequest]:
        return [r for r in self.requests.values() if r.status == "pending"
                and self.accounts.children.get(r.child_id)
                and self.accounts.children[r.child_id].parent_id == parent_id]

    def resolve_request(self, parent_id: str, request_id: str, approve: bool,
                        minutes: Optional[int] = None) -> ExtensionRequest:
        req = self.requests.get(request_id)
        if req is None:
            raise NotFound("request")
        self._owned(parent_id, req.child_id)
        if req.status != "pending":
            raise ValidationFailed("request already resolved")
        if approve:
            self._grant(req.child_id, minutes or req.minutes)
        req.status = "approved" if approve else "rejected"
        req.resolved_at = self._now()
        return req

    def request_status(self, child_id: str, request_id: str) -> str:
        req = self.requests.get(request_id)
        if req is None or req.child_id != child_id:
            raise NotFound("request")
        return req.status
