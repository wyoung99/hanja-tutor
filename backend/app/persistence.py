"""Row codecs: how each service's state maps to the PostgreSQL tables of ``db.state``.

``register_all`` registers one (snapshot, restore) pair per table. ``snapshot`` yields
``(key, child_id, parent_id, doc)`` for the current in-memory state; ``restore`` rebuilds it at startup.
Study plans / review items are restored lazily: the planner object is rebuilt from the child's target the
first time it is needed and the saved state (learned hanja, review items, parent overrides, frozen days)
is applied on top (``PlanState``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, Optional

from accounts.service import ChildProfile, ParentAccount
from billing.service import Payment, Subscription
from chatbot.service import SafetyEvent
from conversations import ThreadMeta
from lifecycle import ArchivedPayment, PaymentRecord
from mockexam.questions import Question
from mockexam.service import Exam
from notifications import InboxItem, PushSubscription
from refunds.service import RefundRequest
from reports.service import _ReviewAnswer
from screentime.service import ExtensionRequest, ScreenTimeSettings, TimeWindow, _Day
from study.planner import DayPlan, ReviewItem, StudyPlanner
from tracing.grader import TracingAttempt, TracingProgress
from usage.service import PaidUsageEvent


# ---- scalar helpers ------------------------------------------------------------------
def dt(x: Optional[datetime]) -> Optional[str]:
    return x.isoformat() if x else None


def pdt(s: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(s) if s else None


def d(x: Optional[date]) -> Optional[str]:
    return x.isoformat() if x else None


def pd(s: Optional[str]) -> Optional[date]:
    return date.fromisoformat(s) if s else None


def _jsonable(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, tuple):
        return [_jsonable(x) for x in v]
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    return v


# ---- accounts ------------------------------------------------------------------------
def parent_doc(p: ParentAccount) -> dict:
    # password_hash is the scrypt string ("scrypt$N$r$p$salt$hash"); plaintext is never part of any row
    return {"id": p.id, "login_id": p.login_id, "password_hash": p.password_hash, "email": p.email,
            "consents": p.consents, "created_at": dt(p.created_at), "last_active_at": dt(p.last_active_at),
            "dormancy_notice_at": dt(p.dormancy_notice_at), "dormant_at": dt(p.dormant_at)}


def parent_from(x: dict) -> ParentAccount:
    # A row from before 2026-11-06 (phone verification, no id/password) loads with login_id/password_hash
    # None: it cannot log in, but its data stays consistent and the dormancy / deletion rules still apply.
    return ParentAccount(x["id"], x.get("login_id"), x.get("password_hash"), x["email"], dict(x["consents"]),
                         pdt(x["created_at"]), pdt(x["last_active_at"]), pdt(x["dormancy_notice_at"]),
                         pdt(x["dormant_at"]))


def child_doc(c: ChildProfile) -> dict:
    return {"id": c.id, "parent_id": c.parent_id, "login_id": c.login_id, "nickname": c.nickname,
            "password_hash": c.password_hash, "ai_consent": c.ai_consent, "failed_attempts": c.failed_attempts,
            "locked": c.locked, "created_at": dt(c.created_at), "ai_consent_at": dt(c.ai_consent_at),
            "extra": _jsonable(c.extra)}


def child_from(x: dict) -> ChildProfile:
    extra = dict(x.get("extra") or {})
    if extra.get("target"):
        extra["target"] = tuple(extra["target"])
    # pre-2026-11-06 PIN profiles load without login_id/password_hash (no login until the parent sets one)
    return ChildProfile(x["id"], x["parent_id"], x.get("login_id"), x["nickname"], x.get("password_hash"),
                        x["ai_consent"], x["failed_attempts"], x["locked"], pdt(x["created_at"]),
                        pdt(x["ai_consent_at"]), extra)


# ---- study plan / review items --------------------------------------------------------
@dataclass
class PlanState:
    """Saved planner state for a child whose planner object is not built yet."""
    plan: Optional[dict] = None
    reviews: Dict[str, dict] = field(default_factory=dict)


def plan_doc(p: StudyPlanner) -> dict:
    return {"start_date": d(p.start_date), "exam_date": d(p.exam_date), "learned": sorted(p.learned),
            "default_override": p.default_override,
            "date_overrides": {d(k): v for k, v in p.date_overrides.items()},
            "frozen": {d(k): {"new_chars": v.new_chars, "quota": v.quota, "days_left": v.days_left,
                              "overridden": v.overridden, "feasible": v.feasible} for k, v in p._frozen.items()}}


def review_doc(r: ReviewItem) -> dict:
    return {"char": r.char, "stage": r.stage, "streak": r.streak, "due": d(r.due), "mastered": r.mastered,
            "last_correct_day": d(r.last_correct_day), "reason": r.reason}


def apply_plan_state(p: StudyPlanner, st: PlanState) -> None:
    if st.plan:
        x = st.plan
        p.start_date = pd(x["start_date"]) or p.start_date
        p.exam_date = pd(x["exam_date"])
        p.learned = set(x["learned"])
        p.default_override = x["default_override"]
        p.date_overrides = {pd(k): v for k, v in x["date_overrides"].items()}
        p._frozen = {pd(k): DayPlan(pd(k), list(v["new_chars"]), v["quota"], v["days_left"], v["overridden"],
                                    v["feasible"]) for k, v in x["frozen"].items()}
    for ch, r in st.reviews.items():
        p.reviews[ch] = ReviewItem(r["char"], r["stage"], r["streak"], pd(r["due"]), r["mastered"],
                                   pd(r["last_correct_day"]), r["reason"])


# ---- exams ------------------------------------------------------------------------------
def exam_doc(e: Exam) -> dict:
    return {"exam_id": e.exam_id, "profile_id": e.profile_id, "body": e.fmt.body, "grade": e.fmt.grade,
            "questions": [{**q.__dict__, "options": list(q.options)} for q in e.questions],
            "started_at": dt(e.started_at), "deadline": dt(e.deadline), "answers": _jsonable(e.answers),
            "submitted_at": dt(e.submitted_at), "auto_submitted": e.auto_submitted, "result": e.result}


def exam_from(x: dict, formats) -> Optional[Exam]:
    fmt = formats.get((x["body"], x["grade"]))
    if fmt is None:
        return None
    qs = [Question(**{**q, "options": tuple(q["options"])}) for q in x["questions"]]
    return Exam(x["exam_id"], x["profile_id"], fmt, qs, pdt(x["started_at"]), pdt(x["deadline"]),
                dict(x["answers"]), pdt(x["submitted_at"]), x["auto_submitted"], x["result"])


# ---- registration ------------------------------------------------------------------------------------
def register_all(sync, *, acc, planners, plan_states, tracings, attempts, sdb, tol, exam, formats, reports,
                 st, bot, conv, usage, bill, refunds, life, center) -> None:
    reg = sync.register

    # accounts (active and dormant accounts live in separate tables: 휴면 = 분리 보관)
    def parents_restore(rows, target):
        for _, _, _, x in rows:
            target[x["id"]] = parent_from(x)

    reg("parents", lambda: ((p.id, None, p.id, parent_doc(p)) for p in list(acc.parents.values())),
        lambda rows: parents_restore(rows, acc.parents))
    reg("dormant_parents", lambda: ((p.id, None, p.id, parent_doc(p)) for p in list(acc.dormant_parents.values())),
        lambda rows: parents_restore(rows, acc.dormant_parents))

    def children_restore(rows, target):
        for _, _, _, x in rows:
            target[x["id"]] = child_from(x)

    reg("children", lambda: ((c.id, c.id, c.parent_id, child_doc(c)) for c in list(acc.children.values())),
        lambda rows: children_restore(rows, acc.children))
    reg("dormant_children", lambda: ((c.id, c.id, c.parent_id, child_doc(c))
                                     for c in list(acc.dormant_children.values())),
        lambda rows: children_restore(rows, acc.dormant_children))

    def sessions_restore(rows):
        for k, _, pid, _ in rows:
            acc._sessions[k] = pid

    reg("parent_sessions", lambda: ((k, None, pid, {}) for k, pid in list(acc._sessions.items())), sessions_restore)

    def child_sessions_snapshot():
        for k, cid in list(acc._child_sessions.items()):
            c = acc.children.get(cid)
            yield k, cid, c.parent_id if c else None, {}

    def child_sessions_restore(rows):
        for k, cid, _, _ in rows:
            acc._child_sessions[k] = cid

    # child_id column: deleting a child deletes its sessions with every other child row
    reg("child_sessions", child_sessions_snapshot, child_sessions_restore)

    # study plans + review items
    def plans_snapshot():
        for cid, p in list(planners.items()):
            yield cid, cid, None, plan_doc(p)
        for cid, s in list(plan_states.items()):
            if cid not in planners and s.plan:
                yield cid, cid, None, s.plan

    def plans_restore(rows):
        for cid, _, _, x in rows:
            plan_states.setdefault(cid, PlanState()).plan = x

    def reviews_snapshot():
        for cid, p in list(planners.items()):
            for ch, r in list(p.reviews.items()):
                yield f"{cid}:{ch}", cid, None, review_doc(r)
        for cid, s in list(plan_states.items()):
            if cid not in planners:
                for ch, r in s.reviews.items():
                    yield f"{cid}:{ch}", cid, None, r

    def reviews_restore(rows):
        for _, cid, _, x in rows:
            plan_states.setdefault(cid, PlanState()).reviews[x["char"]] = x

    reg("study_plans", plans_snapshot, plans_restore)
    reg("review_items", reviews_snapshot, reviews_restore)

    # tracing
    def tracing_restore(rows):
        for cid, _, _, x in rows:
            tracings[cid] = TracingProgress({c: {pd(s) for s in ds} for c, ds in x["passed_days"].items()},
                                            dict(x["retries"]))

    reg("tracing_progress", lambda: ((cid, cid, None, {"passed_days": {c: sorted(d(x) for x in ds)
                                                                       for c, ds in t.passed_days.items()},
                                                       "retries": dict(t.retries)})
                                     for cid, t in list(tracings.items())), tracing_restore)

    def attempts_restore(rows):
        for k, cid, _, x in rows:
            ch = x["char"]
            if ch in sdb:
                attempts[k] = TracingAttempt(sdb[ch], tol, x["current"], {int(i): n for i, n in x["errors"].items()},
                                             x["answer_seen"])

    reg("tracing_attempts", lambda: ((k, k.split(":", 1)[0], None,
                                      {"char": a.data.char, "current": a.current,
                                       "errors": {str(i): n for i, n in a.errors.items()}, "answer_seen": a.answer_seen})
                                     for k, a in list(attempts.items())), attempts_restore)

    # mock exams
    def exams_restore(rows):
        for _, _, _, x in rows:
            e = exam_from(x, formats)
            if e is not None:
                exam.exams[e.exam_id] = e

    reg("mock_exams", lambda: ((e.exam_id, e.profile_id, None, exam_doc(e)) for e in list(exam.exams.values())),
        exams_restore)

    # reports: per-day snapshot of today's hanja, review answers, weekly summary idempotency
    def days_snapshot():
        keys = set(reports._assigned) | set(reports._done)
        for cid, day in sorted(keys):
            yield f"{cid}:{day.isoformat()}", cid, None, {
                "day": day.isoformat(), "assigned": sorted(reports._assigned.get((cid, day), ())),
                "done": sorted(reports._done.get((cid, day), ()))}

    def days_restore(rows):
        for _, cid, _, x in rows:
            day = pd(x["day"])
            if x["assigned"]:
                reports._assigned[(cid, day)] = set(x["assigned"])
            if x["done"]:
                reports._done[(cid, day)] = set(x["done"])

    reg("report_days", days_snapshot, days_restore)
    reg("review_answers", lambda: ((cid, cid, None, {"answers": [[a.day.isoformat(), a.char, a.correct] for a in xs]})
                                   for cid, xs in list(reports._reviews.items())),
        lambda rows: [reports._reviews.__setitem__(cid, [_ReviewAnswer(pd(a[0]), a[1], a[2]) for a in x["answers"]])
                      for _, cid, _, x in rows])
    reg("weekly_summaries_sent", lambda: ((f"{pid}:{w.isoformat()}", None, pid, {"week": w.isoformat()})
                                          for pid, w in list(reports._weekly_sent)),
        lambda rows: [reports._weekly_sent.add((pid, pd(x["week"]))) for _, _, pid, x in rows])

    # screen time
    reg("screentime_settings", lambda: ((cid, cid, None, {"daily_minutes": s.daily_minutes,
                                                          "windows": [[w.start_min, w.end_min] for w in s.windows]})
                                        for cid, s in list(st.settings.items())),
        lambda rows: [st.settings.__setitem__(cid, ScreenTimeSettings(x["daily_minutes"],
                                                                      [TimeWindow(a, b) for a, b in x["windows"]]))
                      for cid, _, _, x in rows])
    reg("screentime_days", lambda: ((f"{cid}:{day.isoformat()}", cid, None,
                                     {"day": day.isoformat(), "used_seconds": v.used_seconds,
                                      "extra_seconds": v.extra_seconds, "override_until": dt(v.override_until),
                                      "last_beat": dt(v.last_beat)})
                                    for (cid, day), v in list(st._days.items())),
        lambda rows: [st._days.__setitem__((cid, pd(x["day"])), _Day(x["used_seconds"], x["extra_seconds"],
                                                                     pdt(x["override_until"]), pdt(x["last_beat"])))
                      for _, cid, _, x in rows])

    # wrong parent passwords typed on a child's device for extra time (a parent login clears them)
    reg("parent_password_failures",
        lambda: ((pid, None, pid, {"failures": n}) for pid, n in list(st._password_failures.items())),
        lambda rows: [st._password_failures.__setitem__(pid, x["failures"]) for pid, _, _, x in rows])
    reg("extension_requests", lambda: ((r.id, r.child_id, None, {**r.__dict__, "created_at": dt(r.created_at),
                                                                 "resolved_at": dt(r.resolved_at)})
                                       for r in list(st.requests.values())),
        lambda rows: [st.requests.__setitem__(k, ExtensionRequest(**{**x, "created_at": pdt(x["created_at"]),
                                                                     "resolved_at": pdt(x["resolved_at"])}))
                      for k, _, _, x in rows])

    # chatbot safety (the blocked text itself is never stored anywhere)
    reg("safety_events", lambda: ((e.id, e.child_id, None, {**e.__dict__, "at": dt(e.at)}) for e in list(bot.events)),
        lambda rows: bot.events.extend(SafetyEvent(**{**x, "at": pdt(x["at"])}) for _, _, _, x in rows))

    def counts_snapshot():
        owner = {e.thread_id: e.child_id for e in list(bot.events)}
        for tid, n in list(bot._thread_counts.items()):
            yield tid, owner.get(tid), None, {"count": n}

    reg("safety_thread_counts", counts_snapshot,
        lambda rows: [bot._thread_counts.__setitem__(k, x["count"]) for k, _, _, x in rows])
    reg("chat_locks", lambda: ((f"{cid}:{day.isoformat()}", cid, None, {"day": day.isoformat()})
                               for cid, day in list(bot._locks)),
        lambda rows: [bot._locks.add((cid, pd(x["day"]))) for _, cid, _, x in rows])

    # usage: paid-use evidence + limit notices (the counters themselves are usage_counters, atomic SQL)
    reg("paid_usage_events", lambda: ((e.reservation_id, e.child_id, None, {**e.__dict__, "at": dt(e.at)})
                                      for e in list(usage.paid_events)),
        lambda rows: usage.paid_events.extend(PaidUsageEvent(**{**x, "at": pdt(x["at"])}) for _, _, _, x in rows))
    reg("usage_limit_notices", lambda: ((f"{cid}:{f}:{s.isoformat()}", cid, None, {"feature": f, "start": dt(s)})
                                        for cid, f, s in list(usage._notified)),
        lambda rows: [usage._notified.add((cid, x["feature"], pdt(x["start"]))) for _, cid, _, x in rows])

    # billing / refunds / retention
    def sub_doc(s: Subscription) -> dict:
        return {**s.__dict__, **{k: dt(getattr(s, k)) for k in ("period_start", "period_end", "confirmed_at",
                                                              "grace_end", "next_retry_at", "ended_at")}}

    def sub_from(x: dict) -> Subscription:
        return Subscription(**{**x, **{k: pdt(x[k]) for k in ("period_start", "period_end", "confirmed_at",
                                                                "grace_end", "next_retry_at", "ended_at")}})

    reg("subscriptions", lambda: ((pid, None, pid, sub_doc(s)) for pid, s in list(bill.subs.items())),
        lambda rows: [bill.subs.__setitem__(pid, sub_from(x)) for pid, _, _, x in rows])
    reg("payments", lambda: ((p.id, None, p.parent_id, {**p.__dict__, "paid_at": dt(p.paid_at)})
                             for p in list(bill.payments)),
        lambda rows: bill.payments.extend(Payment(**{**x, "paid_at": pdt(x["paid_at"])}) for _, _, _, x in rows))
    reg("payment_archive", lambda: ((a.record.id, None, a.record.parent_id,
                                     {"record": {**a.record.__dict__, "paid_at": dt(a.record.paid_at)},
                                      "archived_at": dt(a.archived_at), "delete_after": dt(a.delete_after)})
                                    for a in list(life.archive.rows)),
        lambda rows: life.archive.rows.extend(
            ArchivedPayment(PaymentRecord(**{**x["record"], "paid_at": pdt(x["record"]["paid_at"])}),
                            pdt(x["archived_at"]), pdt(x["delete_after"])) for _, _, _, x in rows))
    reg("refund_requests", lambda: ((r.id, None, r.parent_id, {**r.__dict__, "requested_at": dt(r.requested_at),
                                                               "decided_at": dt(r.decided_at)})
                                    for r in list(refunds.requests.values())),
        lambda rows: [refunds.requests.__setitem__(k, RefundRequest(**{**x, "requested_at": pdt(x["requested_at"]),
                                                                         "decided_at": pdt(x["decided_at"])}))
                      for k, _, _, x in rows])

    # dormant accounts' conversations (separate storage while dormant)
    def vault_snapshot():
        for pid, v in list(life._dormant_vault.items()):
            yield pid, None, pid, {"threads": {tid: {"meta": {**m.__dict__, "created_at": dt(m.created_at),
                                                              "last_message_at": dt(m.last_message_at)},
                                                     "messages": msgs} for tid, (m, msgs) in v.items()}}

    def vault_restore(rows):
        for pid, _, _, x in rows:
            life._dormant_vault[pid] = {
                tid: (ThreadMeta(**{**t["meta"], "created_at": pdt(t["meta"]["created_at"]),
                                    "last_message_at": pdt(t["meta"]["last_message_at"])}), t["messages"])
                for tid, t in x["threads"].items()}

    reg("dormant_vault", vault_snapshot, vault_restore)

    # notifications: in-app inbox + Web Push subscriptions
    def inbox_restore(rows):
        center.inbox.extend(InboxItem(**{**x, "created_at": pdt(x["created_at"])}) for _, _, _, x in rows)
        center.inbox.sort(key=lambda i: i.seq)
        center.restore_seq()

    reg("notifications", lambda: ((i.id, i.child_id, i.parent_id, {**i.__dict__, "created_at": dt(i.created_at)})
                                  for i in list(center.inbox)), inbox_restore)
    reg("push_subscriptions", lambda: ((s.key, None, s.parent_id, {**s.__dict__, "created_at": dt(s.created_at)})
                                       for s in list(center.subscriptions.values())),
        lambda rows: [center.subscriptions.__setitem__(k, PushSubscription(**{**x, "created_at": pdt(x["created_at"])}))
                      for k, _, _, x in rows])


def rebuild_indexes(acc) -> None:
    """Derived account indexes after a restore (login ids of active and dormant accounts)."""
    acc.rebuild_login_index()
