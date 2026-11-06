"""Mock exam sessions: format-exact generation, resume, wall-clock timer, auto-submit, scoring, review feed.

The deadline is absolute (started_at + format minutes), so leaving the app never pauses the
timer. Every read/write first applies expiry: an expired, unsubmitted exam is auto-submitted at
its deadline with whatever answers were saved. `charge` (usage deduction) runs exactly once per
newly started exam, after generation succeeded, and never on resume or auto-submit.
"""
from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from tracing.grader import GradeTolerance, grade_stroke
from tracing.strokes import StrokeData, gradable_chars

from .formats import FORMAT_PENDING_MESSAGE, ExamFormat
from .questions import PILSUN_DATA_CAVEAT, ExamUnavailable, Hanja, Question, generate_questions

KST = timezone(timedelta(hours=9))


class ExamError(Exception):
    pass


class ExamClosed(ExamError):
    """Exam already submitted or timed out."""


class FormatPending(ExamError):
    """Supported grade without a confirmed official format: mock exam blocked ("형식 준비 중")."""


class ActiveExamError(ExamError):
    """Profile already has an unfinished exam of a different body/grade."""


def cumulative_pool(body: str, grade: str, grades_order: Sequence[str],
                    assignments: Mapping[Tuple[str, str], set], hanja: Mapping[str, Hanja]) -> List[Hanja]:
    """Chars assigned to `grade` and every lower grade of the *same* body (no cross-body mapping)."""
    idx = list(grades_order).index(grade)
    chars = set()
    for g in list(grades_order)[: idx + 1]:
        chars |= set(assignments.get((body, g), ()))
    return [hanja[c] for c in sorted(chars) if c in hanja]


@dataclass
class Exam:
    exam_id: str
    profile_id: str
    fmt: ExamFormat
    questions: List[Question]
    started_at: datetime
    deadline: datetime
    answers: Dict[str, object] = field(default_factory=dict)
    submitted_at: Optional[datetime] = None
    auto_submitted: bool = False
    result: Optional[dict] = None

    @property
    def submitted(self) -> bool:
        return self.submitted_at is not None


def format_view(f: ExamFormat) -> dict:
    """Scoring/format facts the client shows (points, official pass line in questions, substitution notice)."""
    return {"minutes": f.minutes, "points_per_question": f.points_per_question, "pass_questions": f.pass_questions,
            "type_mix": f.type_mix, "notice": f.notice,
            "substitutions": {k: {"count": v["count"], "with": v["with"]} for k, v in f.substitutions.items()}}


class MockExamService:
    def __init__(
        self,
        formats: Mapping[Tuple[str, str], ExamFormat],
        pool_for: Callable[[str, str], List[Hanja]],
        stroke_db: Mapping[str, StrokeData],
        clock: Callable[[], datetime] = lambda: datetime.now(KST),
        charge: Optional[Callable[[str], None]] = None,
        on_wrong_char: Optional[Callable[[str, str], None]] = None,  # (profile_id, char) -> review list
        tol: GradeTolerance = GradeTolerance(),
        allow_substitute_writing: bool = False,
        pending: Optional[Mapping[Tuple[str, str], str]] = None,  # formats.load_pending()
    ) -> None:
        self.pending = dict(pending or {})
        self.formats, self.pool_for, self.clock = formats, pool_for, clock
        self.stroke_db = dict(stroke_db)
        self.charge, self.on_wrong_char, self.tol = charge, on_wrong_char, tol
        self.allow_substitute_writing = allow_substitute_writing
        self.exams: Dict[str, Exam] = {}

    # ---- lifecycle -------------------------------------------------------
    def _active(self, profile_id: str) -> Optional[Exam]:
        for e in self.exams.values():
            if e.profile_id == profile_id and not e.submitted:
                self._expire(e)
                if not e.submitted:
                    return e
        return None

    def start(self, profile_id: str, body: str, grade: str, seed: Optional[int] = None) -> Exam:
        fmt = self.formats.get((body, grade))
        if fmt is None:
            if (body, grade) in self.pending:
                raise FormatPending(f"{body}/{grade}: {FORMAT_PENDING_MESSAGE}")
            raise ExamError(f"unsupported exam {body}/{grade}")
        active = self._active(profile_id)
        if active is not None:  # resume: no new questions, no deduction
            if (active.fmt.body, active.fmt.grade) != (body, grade):
                raise ActiveExamError("finish the exam in progress first")
            return active
        pool = self.pool_for(body, grade)
        wchars = gradable_chars(self.stroke_db) if fmt.has_writing else []
        qs = generate_questions(fmt, pool, wchars, random.Random(seed),
                                allow_substitute_writing=self.allow_substitute_writing, strokes=self.stroke_db)
        if self.charge:
            self.charge(profile_id)  # may raise (limit reached) -> nothing created
        now = self.clock()
        exam = Exam(uuid.uuid4().hex, profile_id, fmt, qs, now, now + timedelta(minutes=fmt.minutes))
        self.exams[exam.exam_id] = exam
        return exam

    def _get(self, exam_id: str) -> Exam:
        e = self.exams.get(exam_id)
        if e is None:
            raise ExamError("unknown exam")
        self._expire(e)
        return e

    def _expire(self, e: Exam) -> None:
        if not e.submitted and self.clock() >= e.deadline:
            self._finish(e, e.deadline, auto=True)

    def remaining_seconds(self, exam_id: str) -> int:
        e = self._get(exam_id)
        return 0 if e.submitted else max(0, int((e.deadline - self.clock()).total_seconds()))

    def answer(self, exam_id: str, qid: str, value: object) -> None:
        e = self._get(exam_id)
        if e.submitted:
            raise ExamClosed("exam is closed")
        if qid not in {q.id for q in e.questions}:
            raise ExamError("unknown question")
        e.answers[qid] = value

    def submit(self, exam_id: str) -> dict:
        e = self._get(exam_id)
        if not e.submitted:
            self._finish(e, self.clock(), auto=False)
        return e.result  # type: ignore[return-value]

    def result(self, exam_id: str) -> dict:
        e = self._get(exam_id)
        if not e.submitted:
            raise ExamError("exam not submitted yet")
        return e.result  # type: ignore[return-value]

    def public_view(self, e: Exam) -> dict:
        """What the client may see while the exam is open (no answers/explanations).

        筆順 questions carry the char's stroke medians with the highlighted stroke index (the figure)."""
        qs = []
        for q in e.questions:
            v = {"id": q.id, "type": q.type, "slot": q.slot or q.type, "points": q.points, "prompt": q.prompt,
                 "options": list(q.options)}
            if q.type == "pilsun" and q.char in self.stroke_db:
                v["figure"] = {"strokes": [[list(p) for p in s] for s in self.stroke_db[q.char].strokes],
                               "highlight": q.stroke_index}
            qs.append(v)
        return {"exam_id": e.exam_id, "deadline": e.deadline.isoformat(), **format_view(e.fmt), "questions": qs}

    # ---- grading ---------------------------------------------------------
    def _is_correct(self, q: Question, given: object) -> bool:
        if given is None:
            return False
        if q.type != "writing":
            return given == q.answer
        data = self.stroke_db.get(q.char)
        if data is None or not isinstance(given, (list, tuple)) or len(given) != data.stroke_count:
            return False
        return all(grade_stroke(s, data, i, self.tol) is None for i, s in enumerate(given))

    def _finish(self, e: Exam, at: datetime, auto: bool) -> None:
        e.submitted_at, e.auto_submitted = at, auto
        # per type slot, in format order: official type names (한국어문회) or app-defined labels (대한검정회)
        by_type: Dict[str, dict] = {}
        for t in e.fmt.types:
            by_type[t.key] = {"label": t.label, "type": t.type, "substituted": t.substituted,
                              "correct": 0, "total": 0, "rate": 0.0}
        score, correct_n, wrong = 0, 0, []
        for q in e.questions:
            given = e.answers.get(q.id)
            ok = self._is_correct(q, given)
            slot = by_type.setdefault(q.slot or q.type, {"label": q.slot or q.type, "type": q.type,
                                                         "substituted": False, "correct": 0, "total": 0,
                                                         "rate": 0.0})
            slot["total"] += 1
            if ok:
                score += q.points
                correct_n += 1
                slot["correct"] += 1
            else:
                wrong.append({"id": q.id, "type": q.type, "prompt": q.prompt, "char": q.char,
                              "your_answer": given if q.type != "writing" else ("(작성함)" if given else None),
                              "answer": q.answer, "explanation": q.explanation})
        by_type = {k: v for k, v in by_type.items() if v["total"]}
        for v in by_type.values():
            v["rate"] = v["correct"] / v["total"]
        e.result = {
            "exam_id": e.exam_id, "body": e.fmt.body, "grade": e.fmt.grade,
            "score": score, "total": e.fmt.total_score, "pass_score": e.fmt.pass_score,
            "correct_count": correct_n, "question_count": len(e.questions),
            "passed": score >= e.fmt.pass_score, "auto_submitted": auto,
            **format_view(e.fmt),
            "by_type": by_type, "wrong": wrong,
        }
        if any(q.type == "pilsun" for q in e.questions):  # metadata for parents/operators, not shown to the child
            e.result["data_caveats"] = {"pilsun": PILSUN_DATA_CAVEAT}
        if self.on_wrong_char:
            for c in dict.fromkeys(w["char"] for w in wrong):
                self.on_wrong_char(e.profile_id, c)
