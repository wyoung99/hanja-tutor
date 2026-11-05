"""Stroke grading + tracing attempts + progress.

Order and direction are strict. Position and length use tolerances (settings,
defaults from hanzi-writer: mean-distance threshold 350 of 1000 units, and
length-ratio bounds). A wrong stroke is flagged at once and must be redrawn; the
3rd error on the same stroke reveals the correct stroke. An attempt is shown only
as pass / retry; a pass on 2 different KST days completes writing.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import unicodedata
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Set

from .strokes import Point, StrokeData, is_gradable

KST = timezone(timedelta(hours=9))
REVEAL_AFTER_ERRORS = 3
COMPLETE_DAYS = 2
PASS, RETRY = "pass", "retry"

ERR_ORDER, ERR_DIRECTION, ERR_POSITION, ERR_LENGTH = "order", "direction", "position", "length"


@dataclass(frozen=True)
class GradeTolerance:
    position: float = 350.0  # max mean distance (1000-unit space)
    length_min_ratio: float = 0.5  # drawn/ref length bounds
    length_max_ratio: float = 2.0
    direction_max_angle_deg: float = 60.0  # start->end vector vs reference


def _length(pts: Sequence[Point]) -> float:
    return sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))


def _resample(pts: Sequence[Point], n: int = 24) -> List[Point]:
    pts = list(pts)
    total = _length(pts)
    if len(pts) == 1 or total == 0:
        return [pts[0]] * n
    step = total / (n - 1)
    out: List[Point] = [pts[0]]
    acc = 0.0  # distance covered up to pts[i]
    i = 0
    while len(out) < n - 1 and i < len(pts) - 1:
        seg = math.dist(pts[i], pts[i + 1])
        target = len(out) * step
        if seg > 0 and acc + seg >= target:
            t = (target - acc) / seg
            out.append((pts[i][0] + t * (pts[i + 1][0] - pts[i][0]), pts[i][1] + t * (pts[i + 1][1] - pts[i][1])))
        else:
            acc += seg
            i += 1
    out.append(pts[-1])
    while len(out) < n:
        out.append(pts[-1])
    return out


def _mean_dist(a: Sequence[Point], b: Sequence[Point]) -> float:
    return sum(math.dist(p, q) for p, q in zip(a, b)) / len(a)


def _angle_deg(a: Sequence[Point], b: Sequence[Point]) -> float:
    va = (a[-1][0] - a[0][0], a[-1][1] - a[0][1])
    vb = (b[-1][0] - b[0][0], b[-1][1] - b[0][1])
    na, nb = math.hypot(*va), math.hypot(*vb)
    if na < 1e-9 or nb < 1e-9:
        return 0.0  # dot-like stroke: no meaningful direction
    c = max(-1.0, min(1.0, (va[0] * vb[0] + va[1] * vb[1]) / (na * nb)))
    return math.degrees(math.acos(c))


def grade_stroke(
    drawn: Sequence[Point], data: StrokeData, index: int, tol: GradeTolerance = GradeTolerance()
) -> Optional[str]:
    """None if `drawn` is acceptable as stroke `index`; else the error kind."""
    if not drawn:
        return ERR_POSITION
    ref = data.strokes[index]
    d, r = _resample(drawn), _resample(ref)
    here = _mean_dist(d, r)
    # strict order: the drawing clearly belongs to a later stroke
    if here > tol.position * 0.5:
        for j in range(index + 1, data.stroke_count):
            if _mean_dist(d, _resample(data.strokes[j])) < here * 0.5:
                return ERR_ORDER
    # strict direction: reversed or turned too far from the reference
    if _angle_deg(d, r) > tol.direction_max_angle_deg:
        return ERR_DIRECTION
    rl = _length(ref)
    if rl > 1e-9:
        ratio = _length(drawn) / rl
        if not (tol.length_min_ratio <= ratio <= tol.length_max_ratio):
            return ERR_LENGTH
    if here > tol.position:
        return ERR_POSITION
    return None


@dataclass
class StrokeFeedback:
    ok: bool
    error: Optional[str] = None
    highlight_stroke: bool = False  # wrong stroke is highlighted immediately
    reveal_answer: bool = False  # 3rd error on the same stroke
    stroke_index: int = 0
    done: bool = False


@dataclass
class TracingAttempt:
    data: StrokeData
    tol: GradeTolerance = field(default_factory=GradeTolerance)
    current: int = 0
    errors: Dict[int, int] = field(default_factory=dict)
    answer_seen: bool = False

    @property
    def done(self) -> bool:
        return self.current >= self.data.stroke_count

    def submit(self, drawn: Sequence[Point]) -> StrokeFeedback:
        if self.done:
            return StrokeFeedback(True, stroke_index=self.current, done=True)
        i = self.current
        err = grade_stroke(drawn, self.data, i, self.tol)
        if err is None:
            self.current += 1
            return StrokeFeedback(True, stroke_index=i, done=self.done)
        self.errors[i] = self.errors.get(i, 0) + 1
        reveal = self.errors[i] >= REVEAL_AFTER_ERRORS
        if reveal:
            self.answer_seen = True
        return StrokeFeedback(False, err, True, reveal, i)

    @property
    def result(self) -> Optional[str]:
        """Only 'pass' or 'retry' (never a score); None while unfinished."""
        if not self.done:
            return None
        return RETRY if self.answer_seen else PASS


@dataclass
class TracingProgress:
    """Per-hanja pass days; passes on two different KST days => writing completed."""
    passed_days: Dict[str, Set[date]] = field(default_factory=dict)
    retries: Dict[str, int] = field(default_factory=dict)

    def record(self, char: str, attempt: TracingAttempt, clock: Callable[[], datetime],
               on_answer_seen: Optional[Callable[[str], None]] = None) -> str:
        res = attempt.result
        if res is None:
            raise ValueError("attempt not finished")
        if res == PASS:
            self.passed_days.setdefault(char, set()).add(clock().astimezone(KST).date())
        else:
            self.retries[char] = self.retries.get(char, 0) + 1
            if on_answer_seen:  # e.g. StudyPlanner.add_review(char, REASON_TRACING_ANSWER_SEEN)
                on_answer_seen(char)
        return res

    def completed(self, char: str) -> bool:
        return len(self.passed_days.get(char, ())) >= COMPLETE_DAYS

    def completed_count(self) -> int:
        return sum(1 for c in self.passed_days if self.completed(c))


def start_attempt(char: str, db: Mapping[str, StrokeData],
                  tol: GradeTolerance = GradeTolerance()) -> Dict[str, object]:
    """Gate: gradable chars get a grading attempt; flagged chars get animation only; chars without
    stroke data are not tracing targets ("unavailable")."""
    c = unicodedata.normalize("NFC", char)
    if is_gradable(c, db):
        return {"mode": "graded", "attempt": TracingAttempt(db[c], tol)}
    d = db.get(c)
    if d is None:
        return {"mode": "unavailable", "strokes": ()}
    return {"mode": "animation_only", "strokes": d.strokes}
