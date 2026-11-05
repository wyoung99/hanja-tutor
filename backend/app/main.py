"""Composed FastAPI application (staging / E2E target).

Wires the independently-tested services (accounts, usage, chatbot, mock exam, tracing, study plan,
reports, billing, refunds) behind one HTTP API under ``/api``.

Hanja data is always the real DB: per-body 훈·음 from ``data/hanja_db/hanja_by_body.csv`` and assignments
from ``assignments.csv`` (NFC lookups), stroke medians from ``data/stroke_data/strokes.json`` (hanzi-writer).
Every 훈·음 shown, asked or graded is the value of the child's selected exam body. Grades without a known
official exam format (대한검정회 준5·준4·준3) get learning features only; mock-exam start is blocked.

Staging mode (default, ``HANJA_ENV != "production"``) uses a fake identity verifier
(``POST /api/dev/verify``), a fake Toss gateway (unless ``TOSS_SECRET_KEY`` is set) and a fake LLM
(unless ``LLM_CONFIG`` JSON is provided -> ``llm.build_provider``). Time is injectable for KST-boundary tests.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, Optional

from fastapi import APIRouter, FastAPI, Header, HTTPException
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from accounts.api import create_router as accounts_router  # noqa: E402
from accounts.service import AccountError, AccountService, FakeIdentityVerifier  # noqa: E402
from billing import BillingConfig, BillingService, FakeTossGateway, RecordingEmail, TossHttpGateway  # noqa: E402
from billing.api import create_router as billing_router  # noqa: E402
from chatbot.knowledge import HanjaKnowledge  # noqa: E402
from chatbot.service import ChatbotService, RecordingNotifier  # noqa: E402
from hanja_db.store import HanjaStore, nfc  # noqa: E402
from llm import FakeProvider, LLMRouter, build_llm_router  # noqa: E402
from mockexam import formats as F  # noqa: E402
from mockexam import questions as Q  # noqa: E402
from mockexam.service import ExamError, FormatPending, MockExamService, cumulative_pool  # noqa: E402
from refunds import RefundService  # noqa: E402
from refunds.api import create_router as refunds_router  # noqa: E402
from reports.service import ReportService, SubscriptionRequired  # noqa: E402
from screentime.service import ScreenTimeService  # noqa: E402
from study.planner import PlanHanja, StudyPlanner  # noqa: E402
from tracing.grader import TracingProgress, start_attempt  # noqa: E402
from tracing.strokes import StrokeData, is_gradable, load_db as load_stroke_db  # noqa: E402
from usage import CHATBOT, MOCKEXAM, LimitReached, UsageLimitConfig, UsageService  # noqa: E402

KST = timezone(timedelta(hours=9))
BODY_NAMES = {"korea_eomunhoe": "한국어문회", "daegeom": "대한검정회"}


def hanja_by_body(store: HanjaStore) -> Dict[str, Dict[str, Q.Hanja]]:
    """body -> char -> Hanja(char, hun, eum) with that body's own reading (incomplete rows left out)."""
    out: Dict[str, Dict[str, Q.Hanja]] = {b: {} for b in store.bodies}
    for (body, c), r in store.readings.items():
        if r.complete:
            out.setdefault(body, {})[c] = Q.Hanja(c, r.hun, r.eum)
    return out


class Clock:
    def __init__(self, t: Optional[datetime] = None):
        self.t = t

    def __call__(self) -> datetime:
        return self.t or datetime.now(KST)


class TargetIn(BaseModel):
    body: str
    grade: str


class ChatIn(BaseModel):
    message: str
    thread_id: str = "t1"


class ExamStartIn(BaseModel):
    seed: Optional[int] = None


class AnswerIn(BaseModel):
    question_id: str
    value: object = None


class StrokeIn(BaseModel):
    stroke: list


class ReviewIn(BaseModel):
    correct: bool


def create_app(clock: Optional[Callable[[], datetime]] = None, env: Optional[dict] = None) -> FastAPI:
    env = dict(os.environ if env is None else env)
    clock = clock or Clock()
    production = env.get("HANJA_ENV") == "production"
    app = FastAPI(title="hanja-tutor")

    # ---- data (real DB; no synthetic hanja) -----------------------------------
    store = HanjaStore.load()
    bodies = store.bodies
    formats = F.load_formats()
    pending = F.load_pending()
    assign = store.assignment_sets()
    hanja = hanja_by_body(store)
    sdb: Dict[str, StrokeData] = load_stroke_db()
    kb = HanjaKnowledge.from_store(store)

    # ---- services -----------------------------------------------------------
    ver = FakeIdentityVerifier()
    acc = AccountService(ver, clock)
    push, mail = RecordingNotifier(), RecordingEmail()

    class Late:
        b = None

        def is_paid(self, cid):
            return bool(self.b and self.b.is_paid(cid))

    late = Late()
    usage = UsageService(UsageLimitConfig.from_env(env), clock, late,
                         parent_of=lambda cid: (acc.children[cid].parent_id, acc.children[cid].nickname),
                         notifier=push)
    gw = TossHttpGateway(env["TOSS_SECRET_KEY"]) if env.get("TOSS_SECRET_KEY") else FakeTossGateway()
    bill = BillingService(gw, BillingConfig.from_env(env), clock,
                          parent_info=lambda pid: (acc.parents[pid].email, [c.id for c in acc.children_of(pid)]),
                          usage=usage, notifier=push, email=mail)
    late.b = bill
    refunds = RefundService(bill, usage, clock, notifier=push)
    if env.get("LLM_CONFIG"):
        llm_router = build_llm_router(json.loads(env["LLM_CONFIG"]))  # Haiku by default, Sonnet for reasoning tasks
    else:
        fake = FakeProvider(lambda s, m: "OFF_TOPIC" if "분류기" in s else "學은 배우다라는 뜻이에요. 쉬운 말이에요. 같이 해요.")
        llm_router = LLMRouter({"fast": fake, "reasoning": fake})
    bot = ChatbotService(acc, kb, llm_router.for_task("chat"), None, push, clock,
                         use_llm_classifier=bool(env.get("LLM_CONFIG")), usage=usage,
                         classifier_llm=llm_router.for_task("safety_classifier"))

    planners: Dict[str, StudyPlanner] = {}
    tracings: Dict[str, TracingProgress] = {}
    attempts: Dict[str, object] = {}
    pool = lambda b, g: cumulative_pool(b, g, bodies[b], assign, hanja.get(b, {}))  # noqa: E731
    exam = MockExamService(formats, pool, sdb, clock=clock, charge=usage.exam_charge(),
                           on_wrong_char=lambda pid, c: planner_of(pid).add_review(c), pending=pending)
    st = ScreenTimeService(acc, clock)

    def target_of(cid: str) -> Optional[tuple]:
        t = acc.children[cid].extra.get("target")
        return tuple(t) if t else None

    def planner_of(cid: str) -> StudyPlanner:
        if cid not in planners:
            t = target_of(cid)
            # The DB has no usage-frequency column yet, so the rank is the official list order of the grade.
            chars = store.grade_chars(*t) if t else []
            planners[cid] = StudyPlanner([PlanHanja(c, i + 1) for i, c in enumerate(chars)], clock)
        return planners[cid]

    def reading_for(cid: str, ch: str) -> Optional[dict]:
        """The child's selected body's 훈·음 for ch (never another body's)."""
        t = target_of(cid)
        r = store.reading(t[0], ch) if t else None
        if r is None:
            return None
        return {"hun": r.hun or None, "eum": r.eum or None, "reviewed": r.reviewed}

    def tracing_mode(ch: str) -> str:
        if is_gradable(ch, sdb):
            return "graded"
        return "animation_only" if ch in sdb else "unavailable"

    def card(cid: str, ch: str) -> dict:
        r = reading_for(cid, ch) or {"hun": None, "eum": None, "reviewed": False}
        return {"char": ch, **r, "strokes": sdb[ch].stroke_count if ch in sdb else 0,
                "tracing": tracing_mode(ch)}

    def tracing_of(cid: str) -> TracingProgress:
        return tracings.setdefault(cid, TracingProgress())

    reports = ReportService(acc, bill, st, planner_of, tracing_of, exam, None, usage, push, mail,
                            clock=clock, reading_for=reading_for)

    def parent_of_session(tok: str) -> str:
        return acc.parent_for_session(tok).id

    def pid_from(auth: Optional[str]) -> str:
        try:
            return parent_of_session((auth or "").removeprefix("Bearer ").strip())
        except AccountError:
            raise HTTPException(401, {"code": "login_required"})

    def child_for(auth: Optional[str], child_id: str):
        pid = pid_from(auth) if auth else None
        c = acc.children.get(child_id)
        if c is None or (pid and c.parent_id != pid):
            raise HTTPException(404, {"code": "not_found"})
        return c

    def exam_info(b: str, g: str) -> dict:
        status = F.exam_status(formats, pending, b, g)
        return {"status": status, "message": F.FORMAT_PENDING_MESSAGE if status == "format_pending" else None}

    api = APIRouter(prefix="/api")
    api.include_router(accounts_router(acc))
    api.include_router(billing_router(bill, parent_of_session))
    api.include_router(refunds_router(refunds, parent_of_session, env.get("ADMIN_TOKEN", "")))

    @api.get("/health")
    def health():
        return {"ok": True}

    if not production:
        @api.post("/dev/verify")
        def dev_verify(body: dict):
            return {"verification_token": ver.complete_verification(str(body.get("ci", "ci-dev")))}

    @api.get("/catalog")
    def catalog():
        return [{"body": b, "name": BODY_NAMES.get(b, b), "grades": g,
                 "exam": {x: exam_info(b, x) for x in g}} for b, g in bodies.items()]

    @api.put("/children/{child_id}/target")
    def set_target(child_id: str, body: TargetIn, authorization: Optional[str] = Header(None)):
        c = child_for(authorization, child_id)
        if body.grade not in bodies.get(body.body, []):
            raise HTTPException(422, {"code": "unsupported_grade"})
        c.extra["target"] = (body.body, body.grade)
        planners.pop(child_id, None)
        return {"body": body.body, "grade": body.grade, "exam": exam_info(body.body, body.grade)}

    @api.get("/children/{child_id}/today")
    def today(child_id: str):
        c = child_for(None, child_id)
        if not c.extra.get("target"):
            raise HTTPException(409, {"code": "no_target"})
        s = planner_of(child_id).todays_session()
        b, g = c.extra["target"]
        return {"body": b, "grade": g, "exam": exam_info(b, g),
                "new": [card(child_id, x) for x in s["new"]], "reviews": [card(child_id, x) for x in s["reviews"]]}

    @api.get("/children/{child_id}/hanja/{char}")
    def hanja_card(child_id: str, char: str):
        child_for(None, child_id)
        if not target_of(child_id):
            raise HTTPException(409, {"code": "no_target"})
        ch = nfc(char)
        if reading_for(child_id, ch) is None:
            raise HTTPException(404, {"code": "not_in_body_db"})
        return card(child_id, ch)

    @api.get("/strokes/{char}")
    def strokes(char: str):
        ch = nfc(char)
        d = sdb.get(ch)
        if d is None:
            raise HTTPException(404, {"code": "no_stroke_data"})
        return {"char": ch, "mode": tracing_mode(ch), "strokes": [[list(p) for p in s] for s in d.strokes]}

    @api.post("/children/{child_id}/today/{char}/complete")
    def complete(child_id: str, char: str):
        child_for(None, child_id)
        char = nfc(char)
        try:
            planner_of(child_id).complete_new(char)
            reports.complete_new(child_id, char)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(422, {"code": "cannot_complete", "message": str(e)})
        return {"ok": True}

    @api.post("/children/{child_id}/reviews/{char}")
    def review(child_id: str, char: str, body: ReviewIn):
        child_for(None, child_id)
        char = nfc(char)
        try:
            planner_of(child_id).answer_review(char, body.correct)
            reports.answer_review(child_id, char, body.correct)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(422, {"code": "cannot_review", "message": str(e)})
        return {"ok": True}

    # ---- tracing ------------------------------------------------------------
    @api.post("/children/{child_id}/tracing/{char}/start")
    def trace_start(child_id: str, char: str):
        child_for(None, child_id)
        char = nfc(char)
        r = start_attempt(char, sdb)
        strokes_out = [[list(p) for p in s] for s in (sdb[char].strokes if char in sdb else ())]
        if r["mode"] == "graded":
            attempts[f"{child_id}:{char}"] = r["attempt"]
        # the median polylines double as the tracing guide shown under the pad
        return {"mode": r["mode"], "stroke_count": len(strokes_out), "strokes": strokes_out}

    @api.post("/children/{child_id}/tracing/{char}/stroke")
    def trace_stroke(child_id: str, char: str, body: StrokeIn):
        child_for(None, child_id)
        char = nfc(char)
        att = attempts.get(f"{child_id}:{char}")
        if att is None:
            raise HTTPException(409, {"code": "not_started"})
        fb = att.submit([tuple(p) for p in body.stroke])
        out = {"ok": fb.ok, "error": fb.error, "highlight_stroke": fb.highlight_stroke,
               "reveal_answer": fb.reveal_answer, "stroke_index": fb.stroke_index, "done": fb.done}
        if fb.reveal_answer:  # 3rd error on the same stroke: show the correct stroke
            out["answer_stroke"] = [list(p) for p in att.data.strokes[fb.stroke_index]]
        if fb.done:
            out["result"] = tracing_of(child_id).record(
                char, att, clock, lambda c: planner_of(child_id).add_review(c, "tracing_answer_seen"))
        return out

    # ---- mock exam ----------------------------------------------------------
    @api.post("/children/{child_id}/exams")
    def exam_start(child_id: str, body: ExamStartIn):
        c = child_for(None, child_id)
        t = c.extra.get("target")
        if not t:
            raise HTTPException(409, {"code": "no_target"})
        try:
            e = exam.start(child_id, t[0], t[1], body.seed)
        except LimitReached as e2:
            raise HTTPException(429, {"code": "limit_reached", "resets_at": e2.resets_at.isoformat()})
        except FormatPending:
            raise HTTPException(409, {"code": "exam_format_pending", "message": F.FORMAT_PENDING_MESSAGE})
        except ExamError as e2:
            raise HTTPException(409, {"code": "exam_error", "message": str(e2)})
        v = exam.public_view(e)
        v["minutes"] = e.fmt.minutes
        v["pass_score"] = e.fmt.pass_score
        v["total_score"] = e.fmt.total_score
        return v

    @api.post("/exams/{exam_id}/answer")
    def exam_answer(exam_id: str, body: AnswerIn):
        try:
            exam.answer(exam_id, body.question_id, body.value)
        except ExamError as e:
            raise HTTPException(409, {"code": "exam_closed", "message": str(e)})
        return {"ok": True}

    @api.post("/exams/{exam_id}/submit")
    def exam_submit(exam_id: str):
        try:
            return exam.submit(exam_id)
        except ExamError as e:
            raise HTTPException(409, {"code": "exam_error", "message": str(e)})

    # ---- chatbot ---------------------------------------------------------------
    @api.post("/children/{child_id}/chat")
    def chat(child_id: str, body: ChatIn):
        child_for(None, child_id)
        try:
            r = bot.ask(child_id, body.thread_id, body.message)
        except AccountError as e:
            raise HTTPException(403, {"code": e.code})
        return {"status": r.status, "reply": r.reply, "locked": r.locked_today}

    # ---- reports ------------------------------------------------------------------
    @api.get("/children/{child_id}/report/basic")
    def report_basic(child_id: str, authorization: Optional[str] = Header(None)):
        pid = pid_from(authorization)
        return reports.basic_report(pid, child_id)

    @api.get("/children/{child_id}/report/detailed")
    def report_detailed(child_id: str, authorization: Optional[str] = Header(None)):
        pid = pid_from(authorization)
        try:
            return reports.detailed_report(pid, child_id)
        except SubscriptionRequired:
            raise HTTPException(402, {"code": "subscription_required"})

    @api.get("/children/{child_id}/usage")
    def usage_view(child_id: str):
        child_for(None, child_id)
        return {"chatbot": usage.snapshot(child_id, CHATBOT), "mockexam": usage.snapshot(child_id, MOCKEXAM)}

    @api.get("/children")
    def children(authorization: Optional[str] = Header(None)):
        pid = pid_from(authorization)
        return [{"id": c.id, "nickname": c.nickname, "ai_consent": c.ai_consent,
                 "target": list(c.extra.get("target") or [])} for c in acc.children_of(pid)]

    app.include_router(api)
    app.state.services = dict(acc=acc, usage=usage, bill=bill, refunds=refunds, exam=exam, gateway=gw,
                              llm_router=llm_router, store=store, strokes=sdb, bot=bot, reports=reports)
    return app


app = create_app()
