"""Today's-hanja planner and spaced review (all date boundaries are KST midnight).

Design: the plan is *derived* from (assigned hanja, learned set, today, exam date),
so a skipped day needs no special handling -- the next day's quota is simply
recomputed from what is still unlearned. The set shown for a KST date is frozen
the first time it is requested so it stays stable during that day.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, Iterable, List, Optional

KST = timezone(timedelta(hours=9))
MIN_PER_DAY = 3
MAX_PER_DAY = 15
DEFAULT_PLAN_DAYS = 90
REVIEW_INTERVALS = (1, 3, 7, 14)  # days
MASTER_STREAK = 3  # consecutive correct answers on *different* days => mastered
REASON_MOCK_WRONG = "mock_exam_wrong"
REASON_TRACING_ANSWER_SEEN = "tracing_answer_seen"
REVIEW_REASONS = (REASON_MOCK_WRONG, REASON_TRACING_ANSWER_SEEN)


class PlanError(Exception):
    pass


@dataclass(frozen=True)
class PlanHanja:
    char: str
    freq_rank: int  # 1 = most frequently used; from the reviewed DB


def kst_today(clock: Callable[[], datetime]) -> date:
    return clock().astimezone(KST).date()


def daily_quota(remaining: int, days_left: int) -> int:
    """Even split of remaining hanja over days_left days, clamped to 3..15 (0 if none remain)."""
    if remaining <= 0:
        return 0
    days_left = max(1, days_left)
    base = math.ceil(remaining / days_left)
    return min(MAX_PER_DAY, max(MIN_PER_DAY, base, 0)) if remaining >= MIN_PER_DAY else remaining


@dataclass
class ReviewItem:
    char: str
    stage: int = 0  # index into REVIEW_INTERVALS
    streak: int = 0  # consecutive correct answers
    due: date = field(default_factory=date.today)
    mastered: bool = False
    last_correct_day: Optional[date] = None
    reason: Optional[str] = None


@dataclass
class DayPlan:
    day: date
    new_chars: List[str]
    quota: int
    days_left: int
    overridden: bool
    feasible: bool  # False if the even split exceeds 15/day


class StudyPlanner:
    def __init__(
        self,
        assigned: Iterable[PlanHanja],
        clock: Callable[[], datetime] = lambda: datetime.now(KST),
        exam_date: Optional[date] = None,
        start_date: Optional[date] = None,
    ) -> None:
        self.assigned: Dict[str, PlanHanja] = {h.char: h for h in assigned}
        self.clock = clock
        self.exam_date = exam_date
        self.start_date = start_date or kst_today(clock)
        self.learned: set = set()
        self.reviews: Dict[str, ReviewItem] = {}
        self.default_override: Optional[int] = None  # parent-set daily count
        self.date_overrides: Dict[date, int] = {}
        self._frozen: Dict[date, DayPlan] = {}

    # ---- parent edits --------------------------------------------------
    @staticmethod
    def _check_count(n: int) -> int:
        if not isinstance(n, int) or isinstance(n, bool) or not (MIN_PER_DAY <= n <= MAX_PER_DAY):
            raise PlanError(f"daily count must be an integer {MIN_PER_DAY}-{MAX_PER_DAY}")
        return n

    def set_exam_date(self, exam_date: Optional[date]) -> None:
        self.exam_date = exam_date
        self._unfreeze_today()

    def set_daily_override(self, n: Optional[int]) -> None:
        """Parent sets the per-day count from today on (None clears it)."""
        self.default_override = None if n is None else self._check_count(n)
        self._unfreeze_today()

    def set_date_override(self, day: date, n: Optional[int]) -> None:
        if day < kst_today(self.clock):
            raise PlanError("cannot edit a past day")
        if n is None:
            self.date_overrides.pop(day, None)
        else:
            self.date_overrides[day] = self._check_count(n)
        self._frozen.pop(day, None)

    def _unfreeze_today(self) -> None:
        self._frozen.pop(kst_today(self.clock), None)

    # ---- plan ----------------------------------------------------------
    def remaining(self) -> List[PlanHanja]:
        rest = [h for c, h in self.assigned.items() if c not in self.learned]
        return sorted(rest, key=lambda h: (h.freq_rank, h.char))  # frequent first

    def _days_left(self, today: date) -> int:
        end = self.exam_date or (self.start_date + timedelta(days=DEFAULT_PLAN_DAYS - 1))
        if self.exam_date is None and today > end:
            end = today  # default window elapsed: spread the rest over one more day
        return max(1, (end - today).days + 1)

    def plan_for_today(self) -> DayPlan:
        today = kst_today(self.clock)
        frozen = self._frozen.get(today)
        if frozen is not None:
            return frozen
        rest = self.remaining()
        days_left = self._days_left(today)
        even = daily_quota(len(rest), days_left)
        override = self.date_overrides.get(today, self.default_override)
        quota = min(override, len(rest)) if override is not None else even
        raw = math.ceil(len(rest) / days_left) if rest else 0
        plan = DayPlan(
            day=today,
            new_chars=[h.char for h in rest[:quota]],
            quota=quota,
            days_left=days_left,
            overridden=override is not None,
            feasible=raw <= MAX_PER_DAY,
        )
        self._frozen[today] = plan
        return plan

    def projected_schedule(self) -> Dict[date, int]:
        """Per-day new-hanja counts from today to the end of the plan (re-derived from state)."""
        today = kst_today(self.clock)
        left = len(self.remaining())
        out: Dict[date, int] = {}
        day, days_left = today, self._days_left(today)
        while left > 0 and len(out) < 4000:
            n = daily_quota(left, max(1, days_left))
            ov = self.date_overrides.get(day, self.default_override)
            if ov is not None:
                n = min(ov, left)
            out[day] = n
            left -= n
            day += timedelta(days=1)
            days_left -= 1
        return out

    # ---- learning + review ---------------------------------------------
    def complete_new(self, char: str) -> None:
        """Mark a new hanja as learned today; schedules its first review for tomorrow."""
        if char not in self.assigned:
            raise PlanError(f"{char!r} is not in the assigned set")
        if char in self.learned:
            return
        self.learned.add(char)
        today = kst_today(self.clock)
        self.reviews[char] = ReviewItem(char, 0, 0, today + timedelta(days=REVIEW_INTERVALS[0]))

    def due_reviews(self) -> List[ReviewItem]:
        """All due reviews, overdue ones included (a skipped day's backlog carries over), oldest first."""
        today = kst_today(self.clock)
        items = [r for r in self.reviews.values() if not r.mastered and r.due <= today]
        return sorted(items, key=lambda r: (r.due, r.char))

    def add_review(self, char: str, reason: str = REASON_MOCK_WRONG) -> ReviewItem:
        """Entry point for callers (mock exam wrong answers, tracing where the answer stroke was shown).

        Independent of the exam/tracing modules. There is no per-day cap on additions.
        New item -> first interval (1 day). Already tracked -> treated like a miss
        (one stage back); a mastered item re-enters at the first stage.
        """
        if char not in self.assigned:
            raise PlanError(f"{char!r} is not in the assigned set")
        if reason not in REVIEW_REASONS:
            raise PlanError(f"unknown review reason {reason!r}")
        today = kst_today(self.clock)
        item = self.reviews.get(char)
        if item is None:
            item = ReviewItem(char, 0, 0, today + timedelta(days=REVIEW_INTERVALS[0]), reason=reason)
            self.reviews[char] = item
            return item
        item.reason = reason
        if item.mastered:
            item.mastered = False
            item.stage = 0
        else:
            item.stage = max(0, item.stage - 1)
        item.streak = 0
        item.last_correct_day = None
        item.due = today + timedelta(days=REVIEW_INTERVALS[item.stage])
        return item

    def answer_review(self, char: str, correct: bool) -> ReviewItem:
        item = self.reviews.get(char)
        if item is None:
            raise PlanError(f"no review item for {char!r}")
        today = kst_today(self.clock)
        if correct:
            if item.last_correct_day == today:
                return item  # same-day repeat does not count toward the streak
            item.last_correct_day = today
            item.streak += 1
            if item.streak >= MASTER_STREAK:
                item.mastered = True
            else:
                item.stage = min(item.stage + 1, len(REVIEW_INTERVALS) - 1)
                item.due = today + timedelta(days=REVIEW_INTERVALS[item.stage])
        else:
            item.streak = 0
            item.last_correct_day = None
            item.stage = max(0, item.stage - 1)  # one step back, not a full reset
            item.due = today + timedelta(days=REVIEW_INTERVALS[item.stage])
        return item

    def todays_session(self) -> Dict[str, object]:
        """What the child sees: the day's new hanja plus every due review."""
        plan = self.plan_for_today()
        return {"new": plan.new_chars, "reviews": [r.char for r in self.due_reviews()], "plan": plan}
