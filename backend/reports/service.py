"""Parent reports.

* Basic report (study time, today's-hanja completion rate, review accuracy) and conversation
  viewing are ALWAYS available to every parent (safety/supervision is never paywalled).
* Detailed report (weak hanja, mock-exam score trend, pass likelihood, completed-writing count)
  and the weekly PWA push summary are subscriber-only.
* A detailed-report view by a subscriber is logged as a paid-use event (refund judgement input).
* Pass likelihood = mean of the latest 3 mock-exam scores minus the pass line.
* Parents without web push receive the weekly summary by email instead.
* All day/week boundaries are KST (week starts Monday 00:00), via the injected clock.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Set

KST = timezone(timedelta(hours=9))
PASS_WINDOW = 3
DEFAULT_DAYS = 7
DETAILED_REPORT = "detailed_report"


class SubscriptionRequired(Exception):
    """The detailed report / weekly summary needs an active subscription."""


@dataclass
class _ReviewAnswer:
    day: date
    char: str
    correct: bool


class ReportService:
    def __init__(
        self,
        accounts,
        billing,                      # needs is_parent_paid(parent_id)
        screentime,                   # needs used_seconds(child_id, day)
        planner_for: Callable[[str], object],   # child_id -> StudyPlanner
        tracing_for: Callable[[str], object],   # child_id -> TracingProgress
        mockexam=None,                # MockExamService (submitted exams are the score history)
        conversations=None,           # ConversationService
        usage=None,                   # UsageService.record_paid_event
        notifier=None,                # push: notify(parent_id, kind, payload)
        email=None,                   # send(to, subject, body)
        can_push: Callable[[str], bool] = lambda parent_id: True,
        clock: Callable[[], datetime] = lambda: datetime.now(KST),
        reading_for: Optional[Callable[[str, str], Optional[dict]]] = None,  # (child, char) -> body's 훈·음
    ) -> None:
        self.reading_for = reading_for
        self.accounts, self.billing, self.screentime = accounts, billing, screentime
        self.planner_for, self.tracing_for = planner_for, tracing_for
        self.mockexam, self.conversations, self.usage = mockexam, conversations, usage
        self.notifier, self.email, self.can_push, self.clock = notifier, email, can_push, clock
        self._assigned: Dict[tuple, Set[str]] = {}       # (child, day) -> today's new hanja
        self._done: Dict[tuple, Set[str]] = {}           # (child, day) -> completed new hanja
        self._reviews: Dict[str, List[_ReviewAnswer]] = {}
        self._weekly_sent: Set[tuple] = set()            # (parent, week_start)

    # ---- helpers --------------------------------------------------------
    def _today(self) -> date:
        return self.clock().astimezone(KST).date()

    def _days(self, days: int) -> List[date]:
        t = self._today()
        return [t - timedelta(days=i) for i in range(days - 1, -1, -1)]

    # ---- activity recording (called by the learning flows) -----------------
    def start_day(self, child_id: str) -> List[str]:
        """Snapshot today's new hanja (the denominator of the completion rate)."""
        plan = self.planner_for(child_id).plan_for_today()
        self._assigned.setdefault((child_id, self._today()), set()).update(plan.new_chars)
        return list(plan.new_chars)

    def complete_new(self, child_id: str, char: str) -> None:
        self.start_day(child_id)
        self.planner_for(child_id).complete_new(char)
        if char in self._assigned[(child_id, self._today())]:
            self._done.setdefault((child_id, self._today()), set()).add(char)

    def answer_review(self, child_id: str, char: str, correct: bool):
        item = self.planner_for(child_id).answer_review(char, correct)
        self._reviews.setdefault(child_id, []).append(_ReviewAnswer(self._today(), char, bool(correct)))
        return item

    # ---- basic report (free, unlimited) --------------------------------------
    def basic_report(self, parent_id: str, child_id: str, days: int = DEFAULT_DAYS) -> dict:
        self.accounts._owned(parent_id, child_id)
        span = self._days(days)
        per_day = [{"date": d.isoformat(),
                    "study_minutes": round(self.screentime.used_seconds(child_id, d) / 60, 1)} for d in span]
        assigned = sum(len(self._assigned.get((child_id, d), ())) for d in span)
        done = sum(len(self._done.get((child_id, d), ())) for d in span)
        in_span = set(span)
        answers = [a for a in self._reviews.get(child_id, []) if a.day in in_span]
        right = sum(1 for a in answers if a.correct)
        return {
            "child_id": child_id, "days": days, "from": span[0].isoformat(), "to": span[-1].isoformat(),
            "study_minutes_total": round(sum(x["study_minutes"] for x in per_day), 1),
            "study_minutes_per_day": per_day,
            "todays_hanja": {"assigned": assigned, "completed": done,
                             "completion_rate": (done / assigned) if assigned else None},
            "review": {"answered": len(answers), "correct": right,
                       "accuracy": (right / len(answers)) if answers else None},
        }

    # ---- conversation viewing (free, unlimited) --------------------------------
    def conversations_list(self, parent_id: str, child_id: str):
        return self.conversations.parent_list_threads(parent_id, child_id)

    def conversation_messages(self, parent_id: str, child_id: str, thread_id: str):
        return self.conversations.parent_get_messages(parent_id, child_id, thread_id)

    # ---- detailed report (subscribers) ------------------------------------------
    def _detail(self, child_id: str, days: int) -> dict:
        span = set(self._days(days))
        wrong: Counter = Counter()
        for a in self._reviews.get(child_id, []):
            if a.day in span and not a.correct:
                wrong[a.char] += 1
        exams = []
        if self.mockexam is not None:
            exams = sorted((e for e in self.mockexam.exams.values()
                            if e.profile_id == child_id and e.submitted and e.result),
                           key=lambda e: e.submitted_at)
        for e in exams:
            if e.submitted_at.astimezone(KST).date() in span:
                for w in e.result["wrong"]:
                    wrong[w["char"]] += 1
        tr = self.tracing_for(child_id)
        for c, n in tr.retries.items():
            if n:
                wrong[c] += n
        trend = [{"exam_id": e.exam_id, "at": e.submitted_at.astimezone(KST).isoformat(),
                  "body": e.result["body"], "grade": e.result["grade"], "score": e.result["score"],
                  "total": e.result["total"], "pass_score": e.result["pass_score"],
                  "passed": e.result["passed"]} for e in exams]
        passing = None
        if trend:
            recent = trend[-PASS_WINDOW:]
            avg = sum(t["score"] for t in recent) / len(recent)
            margin = avg - recent[-1]["pass_score"]
            passing = {"based_on": len(recent), "average_score": avg,
                       "pass_score": recent[-1]["pass_score"], "margin": margin,
                       "likely_pass": margin >= 0}
        return {
            "child_id": child_id, "days": days,
            "weak_hanja": [self._weak(child_id, c, n) for c, n in
                           sorted(wrong.items(), key=lambda kv: (-kv[1], kv[0]))],
            "mock_exam_trend": trend,
            "pass_likelihood": passing,
            "writing_completed_count": tr.completed_count(),
        }

    def _weak(self, child_id: str, char: str, misses: int) -> dict:
        out = {"char": char, "misses": misses}
        r = self.reading_for(child_id, char) if self.reading_for else None
        if r:  # the child's selected exam body's 훈·음
            out["hun"], out["eum"] = r.get("hun"), r.get("eum")
        return out

    def detailed_report(self, parent_id: str, child_id: str, days: int = 30) -> dict:
        self.accounts._owned(parent_id, child_id)
        if not self.billing.is_parent_paid(parent_id):
            raise SubscriptionRequired("detailed report is subscriber-only")
        out = self._detail(child_id, days)
        if self.usage is not None:  # viewing counts as paid use (refund evidence)
            self.usage.record_paid_event(child_id, DETAILED_REPORT)
        return out

    # ---- weekly summary (subscribers; PWA push, email fallback) -------------------
    def send_weekly_summaries(self) -> List[str]:
        """Run weekly (idempotent per KST week). Returns the parent ids notified."""
        today = self._today()
        week = today - timedelta(days=today.weekday())
        sent: List[str] = []
        for pid, parent in list(self.accounts.parents.items()):
            if (pid, week) in self._weekly_sent or not self.billing.is_parent_paid(pid):
                continue
            kids = []
            for c in self.accounts.children_of(pid):
                b = self.basic_report(pid, c.id, 7)
                d = self._detail(c.id, 7)  # summary generation is not a parent "view"
                kids.append({"child_id": c.id, "nickname": c.nickname,
                             "study_minutes": b["study_minutes_total"],
                             "completion_rate": b["todays_hanja"]["completion_rate"],
                             "review_accuracy": b["review"]["accuracy"],
                             "weak_hanja": [w["char"] for w in d["weak_hanja"][:5]],
                             "likely_pass": (d["pass_likelihood"] or {}).get("likely_pass"),
                             "writing_completed": d["writing_completed_count"]})
            msg = ("주간 학습 요약: " + ", ".join(f"{k['nickname']} {k['study_minutes']:.0f}분" for k in kids)
                   if kids else "주간 학습 요약")
            payload = {"message": msg, "week_start": week.isoformat(), "children": kids}
            if self.can_push(pid) and self.notifier:
                self.notifier.notify(pid, "weekly_summary", payload)
            elif self.email and parent.email:
                self.email.send(parent.email, "주간 학습 요약", msg)
            else:
                continue
            self._weekly_sent.add((pid, week))
            sent.append(pid)
        return sent
