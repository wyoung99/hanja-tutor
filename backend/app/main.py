"""Composed FastAPI application (local test environment / E2E target).

Wires the independently-tested services (accounts, lifecycle, usage, chatbot + conversations, mock exam,
tracing, study plan, screen time, reports, billing, refunds) behind one HTTP API under ``/api``.

Hanja data is always the real DB: per-body 훈·음 from ``data/hanja_db/hanja_by_body.csv`` and assignments
from ``assignments.csv`` (NFC lookups), stroke medians from ``data/stroke_data/strokes.json`` (hanzi-writer).
Every 훈·음 shown, asked or graded is the value of the child's selected exam body. Mock-exam formats come
from ``data/exam_formats.json`` (generated from the official format files of both bodies); a grade without a
known official format would get learning features only (mock-exam start blocked, "형식 준비 중").

Configuration (``backend/.env`` + process env, see ``app/settings.py`` and ``.env.example``):
* ``LLM_CONFIG`` JSON -> ``llm.build_llm_router`` (Bedrock Haiku/Sonnet by tier); empty -> fake LLM.
  Bedrock credentials come only from the default boto3 chain; region from LLM_CONFIG or AWS_REGION.
* ``OPENDICT_API_KEY`` -> 우리말샘 fallback dictionary; empty -> unknown hanja answer "잘 모르겠어요".
* ``DATABASE_URL`` or ``PG_EMBEDDED_DIR`` -> PostgreSQL is the system of record: LangGraph PostgresSaver
  checkpoints + ``chat_threads``, atomic ``usage_counters``, and one table per entity for accounts, profiles,
  consents, plans/review items, tracing, mock exams, screen time, safety events, billing, refunds, payment
  archive, notifications and push subscriptions (``db.state`` / ``app.persistence``; flushed before every
  response). Neither -> everything in memory (unit tests).
* ``VAPID_PUBLIC_KEY`` / ``VAPID_PRIVATE_KEY`` / ``VAPID_SUBJECT`` -> real Web Push to parents' browsers
  (``notifications``); missing -> in-app inbox + email fallback only.
* ``STROKE_TOL_*`` -> stroke grading tolerances (library defaults otherwise).
Non-production (``HANJA_ENV != "production"``) adds a fake identity verifier (``POST /api/dev/verify``) and a
clock shift endpoint (``POST /api/dev/clock``) for timer tests; Toss is faked unless ``TOSS_SECRET_KEY`` is set.
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
import sys
import threading
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

from fastapi import APIRouter, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from accounts.api import create_router as accounts_router  # noqa: E402
from accounts.service import AccountError, AccountService, FakeIdentityVerifier  # noqa: E402
from app.settings import aws_region, clean, load_runtime_env  # noqa: E402
from billing import BillingConfig, BillingService, FakeTossGateway, RecordingEmail, TossHttpGateway  # noqa: E402
from billing.api import create_router as billing_router  # noqa: E402
from chatbot.dictionary import UrimalsaemClient  # noqa: E402
from chatbot.knowledge import HanjaKnowledge  # noqa: E402
from chatbot.service import ChatbotService  # noqa: E402
from chatbot.streaming import sse  # noqa: E402
from conversations import (ConversationService, InMemoryCheckpointer, InMemoryThreadMeta,  # noqa: E402
                           PostgresCheckpointer, PostgresThreadMeta)
from db.pg import database_url  # noqa: E402
from db.state import PostgresStateBackend, StateSync  # noqa: E402
from app.persistence import PlanState, apply_plan_state, rebuild_indexes, register_all  # noqa: E402
from notifications import InvalidSubscription, NotificationCenter, VapidConfig, WebPushSender  # noqa: E402
from hanja_db.store import HanjaStore, load_eomunhoe_attributes, nfc  # noqa: E402
from lifecycle import LifecycleService, PaymentRecord, PaymentStore  # noqa: E402
from llm import FakeProvider, LLMRouter, build_llm_router  # noqa: E402
from mockexam import formats as F  # noqa: E402
from mockexam import questions as Q  # noqa: E402
from mockexam.service import ExamError, FormatPending, MockExamService, cumulative_pool  # noqa: E402
from refunds import RefundService  # noqa: E402
from refunds.api import create_router as refunds_router  # noqa: E402
from reports.service import ReportService, SubscriptionRequired  # noqa: E402
from screentime.service import ScreenTimeError, ScreenTimeService  # noqa: E402
from study.planner import PlanError, PlanHanja, StudyPlanner, kst_today  # noqa: E402
from tracing.grader import GradeTolerance, TracingProgress, start_attempt  # noqa: E402
from tracing.strokes import StrokeData, is_gradable, load_db as load_stroke_db  # noqa: E402
from usage import (CHATBOT, MOCKEXAM, LimitReached, PostgresCounterStore, UsageLimitConfig,  # noqa: E402
                   UsageService)

log = logging.getLogger("hanja.app")
KST = timezone(timedelta(hours=9))
BODY_NAMES = {"korea_eomunhoe": "한국어문회", "daegeom": "대한검정회"}
SCHEDULER_INTERVAL_SECONDS = 3600


def hanja_by_body(store: HanjaStore, attrs: Optional[Dict[str, tuple]] = None) -> Dict[str, Dict[str, Q.Hanja]]:
    """body -> char -> Hanja(char, hun, eum) with that body's own reading (incomplete rows left out).

    한국어문회 chars also get 部首 / total strokes from the 한국어문회 list (部首·筆順 questions)."""
    attrs = load_eomunhoe_attributes() if attrs is None else attrs
    out: Dict[str, Dict[str, Q.Hanja]] = {b: {} for b in store.bodies}
    for (body, c), r in store.readings.items():
        if r.complete:
            rad, tot = attrs.get(c, ("", 0)) if body == "korea_eomunhoe" else ("", 0)
            out.setdefault(body, {})[c] = Q.Hanja(c, r.hun, r.eum, rad, tot)
    return out


class Clock:
    """Wall clock (KST) with an optional fixed time and a dev-only offset (timer / auto-submit tests)."""

    def __init__(self, t: Optional[datetime] = None):
        self.t = t
        self.offset = timedelta(0)

    def __call__(self) -> datetime:
        return (self.t or datetime.now(KST)) + self.offset


class BillingPaymentStore(PaymentStore):
    """Lifecycle view of BillingService.payments: on account deletion the parent's payment rows leave the
    operational store and go to the separate 5-year archive (전자상거래법)."""

    class _Records:
        def __init__(self, bill: BillingService):
            self.bill = bill

        def values(self) -> List[PaymentRecord]:
            return [PaymentRecord(p.id, p.parent_id, p.amount, p.paid_at,
                                  {"plan_id": p.plan_id, "order_id": p.order_id, "payment_key": p.payment_key,
                                   "refunded": p.refunded}) for p in self.bill.payments]

        def pop(self, rid: str) -> PaymentRecord:
            rec = next(r for r in self.values() if r.id == rid)
            self.bill.payments[:] = [p for p in self.bill.payments if p.id != rid]
            return rec

    def __init__(self, bill: BillingService) -> None:
        self.records = self._Records(bill)  # type: ignore[assignment]


# ---- request bodies ------------------------------------------------------------------
class TargetIn(BaseModel):
    body: str
    grade: str


class ChatIn(BaseModel):
    message: str
    thread_id: Optional[str] = None


class ExamStartIn(BaseModel):
    seed: Optional[int] = None


class AnswerIn(BaseModel):
    question_id: str
    value: object = None


class StrokeIn(BaseModel):
    stroke: list


class ReviewIn(BaseModel):
    correct: bool


class PlanIn(BaseModel):
    exam_date: Optional[str] = None      # YYYY-MM-DD (KST); null clears -> 90-day default
    daily_count: Optional[int] = None    # 3..15; null clears -> even split


class ScreenTimeIn(BaseModel):
    daily_minutes: Optional[int] = None
    windows: List[List[str]] = []        # [["15:00", "18:00"], ...]


class ParentPinIn(BaseModel):
    pin: str


class ExtraTimeIn(BaseModel):
    parent_pin: str
    minutes: int


class ExtensionIn(BaseModel):
    minutes: int = 15


class ResolveIn(BaseModel):
    approve: bool
    minutes: Optional[int] = None


class PushKeysIn(BaseModel):
    p256dh: str
    auth: str


class PushSubscriptionIn(BaseModel):
    endpoint: str
    keys: PushKeysIn


class PushEndpointIn(BaseModel):
    endpoint: str


class ClockIn(BaseModel):
    advance_minutes: float = 0
    reset: bool = False


def _build_llm(env: Dict[str, str]) -> LLMRouter:
    if env.get("LLM_CONFIG"):
        cfg = json.loads(env["LLM_CONFIG"])
        if "tiers" not in cfg and cfg.get("provider", "bedrock") == "bedrock" and "region" not in cfg:
            cfg["region"] = aws_region(env)
        return build_llm_router(cfg)  # Haiku by default, Sonnet for reasoning tasks
    fake = FakeProvider(lambda s, m: "OFF_TOPIC" if "분류기" in s else "學은 배우다라는 뜻이에요. 쉬운 말이에요. 같이 해요.")
    return LLMRouter({"fast": fake, "reasoning": fake})


class PersistMiddleware:
    """Pure ASGI: persist the state change of a request BEFORE its response starts (an acknowledged request
    is durable), and once more after streamed bodies / background work finished."""

    def __init__(self, app, flush: Callable[[], int]) -> None:
        self.app, self.flush = app, flush

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        import anyio

        async def send_wrapper(msg):
            if msg["type"] == "http.response.start":
                await anyio.to_thread.run_sync(self.flush)
            await send(msg)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            try:
                await anyio.to_thread.run_sync(self.flush)
            except Exception:  # noqa: BLE001
                log.exception("state flush after response failed")


def create_app(clock: Optional[Callable[[], datetime]] = None, env: Optional[dict] = None,
               scheduler: bool = False, push_sender=None) -> FastAPI:
    """push_sender: tests inject a fake Web Push sender; otherwise VAPID keys in env enable WebPushSender."""
    env = load_runtime_env() if env is None else clean(env)
    clock = clock or Clock()
    production = env.get("HANJA_ENV") == "production"
    admin_token = env.get("ADMIN_TOKEN", "")

    # ---- data (real DB; no synthetic hanja) -----------------------------------
    store = HanjaStore.load()
    bodies = store.bodies
    formats = F.load_formats()
    pending = F.load_pending()
    assign = store.assignment_sets()
    hanja = hanja_by_body(store)
    sdb: Dict[str, StrokeData] = load_stroke_db()
    kb = HanjaKnowledge.from_store(store)
    tol = GradeTolerance.from_env(env)

    # ---- services -----------------------------------------------------------
    ver = FakeIdentityVerifier()
    acc = AccountService(ver, clock)
    mail = RecordingEmail()
    db_url = database_url(env)
    sync = StateSync(PostgresStateBackend(db_url) if db_url else None)

    # parent notifications: inbox + Web Push (VAPID) + email fallback
    vapid = VapidConfig.from_env(env)
    if push_sender is None and vapid is not None:
        push_sender = WebPushSender(vapid)
    push_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="webpush") if isinstance(
        push_sender, WebPushSender) else None

    def email_of(pid: str) -> Optional[str]:
        p = acc.parents.get(pid) or acc.dormant_parents.get(pid)
        return p.email if p else None

    def nickname_of(cid: str) -> Optional[str]:
        c = acc.children.get(cid)
        return c.nickname if c else None

    push = NotificationCenter(push_sender, email=mail, email_of=email_of, nickname_of=nickname_of, clock=clock,
                              executor=push_pool, allow_local_endpoints=not production,
                              after_deliver=sync.flush)

    class Late:
        b = None

        def is_paid(self, cid):
            return bool(self.b and self.b.is_paid(cid))

    late = Late()
    usage = UsageService(UsageLimitConfig.from_env(env), clock, late,
                         parent_of=lambda cid: (acc.children[cid].parent_id, acc.children[cid].nickname),
                         notifier=push, counters=PostgresCounterStore(db_url) if db_url else None)
    gw = TossHttpGateway(env["TOSS_SECRET_KEY"]) if env.get("TOSS_SECRET_KEY") else FakeTossGateway()
    bill = BillingService(gw, BillingConfig.from_env(env), clock,
                          parent_info=lambda pid: (acc.parents[pid].email, [c.id for c in acc.children_of(pid)]),
                          usage=usage, notifier=push, email=mail)
    late.b = bill
    refunds = RefundService(bill, usage, clock, notifier=push)
    llm_router = _build_llm(env)
    dictionary = UrimalsaemClient(env["OPENDICT_API_KEY"]) if env.get("OPENDICT_API_KEY") else None
    bot = ChatbotService(acc, kb, llm_router.for_task("chat"), dictionary, push, clock,
                         use_llm_classifier=bool(env.get("LLM_CONFIG")), usage=usage,
                         classifier_llm=llm_router.for_task("safety_classifier"))

    if db_url:  # LangGraph PostgresSaver + chat_threads metadata table
        conv = ConversationService(bot, acc, PostgresCheckpointer.from_conn_string(db_url), PostgresThreadMeta(db_url))
    else:
        conv = ConversationService(bot, acc, InMemoryCheckpointer(), InMemoryThreadMeta())

    planners: Dict[str, StudyPlanner] = {}
    plan_states: Dict[str, PlanState] = {}  # saved planner state not rebuilt yet (restart)
    tracings: Dict[str, TracingProgress] = {}
    attempts: Dict[str, object] = {}
    pool = lambda b, g: cumulative_pool(b, g, bodies[b], assign, hanja.get(b, {}))  # noqa: E731

    def on_wrong_char(cid: str, ch: str) -> None:
        try:
            planner_of(cid).add_review(ch)
        except PlanError as e:  # never fail grading because of the review feed
            log.warning("review feed skipped %s: %s", ch, e)

    exam = MockExamService(formats, pool, sdb, clock=clock, charge=usage.exam_charge(),
                           on_wrong_char=on_wrong_char, pending=pending, tol=tol)
    st = ScreenTimeService(acc, clock, exam_in_progress=lambda cid: exam._active(cid) is not None,
                           notify_parent=push.notify)

    def target_of(cid: str) -> Optional[tuple]:
        t = acc.children[cid].extra.get("target")
        return tuple(t) if t else None

    def cumulative_chars(b: str, g: str) -> set:
        order = list(bodies[b])
        out: set = set()
        for x in order[: order.index(g) + 1]:
            out |= set(assign.get((b, x), ()))
        return out

    def planner_of(cid: str) -> StudyPlanner:
        if cid not in planners:
            t = target_of(cid)
            # The DB has no usage-frequency column yet, so the rank is the official list order of the grade.
            chars = store.grade_chars(*t) if t else []
            extra = acc.children[cid].extra
            ed = extra.get("exam_date")
            p = StudyPlanner([PlanHanja(c, i + 1) for i, c in enumerate(chars)], clock,
                             exam_date=date.fromisoformat(ed) if ed else None,
                             review_chars=cumulative_chars(*t) if t else ())
            if extra.get("daily_count") is not None:
                p.set_daily_override(int(extra["daily_count"]))
            saved = plan_states.pop(cid, None)
            if saved is not None:
                apply_plan_state(p, saved)
            planners[cid] = p
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

    # weekly summary: always handed to the notification center, which pushes or falls back to email
    reports = ReportService(acc, bill, st, planner_of, tracing_of, exam, conv, usage, push, mail,
                            can_push=lambda pid: True, clock=clock, reading_for=reading_for)

    # ---- lifecycle (deletion / dormancy) --------------------------------------------
    life = LifecycleService(acc, conv, push, payments=BillingPaymentStore(bill), clock=clock)

    def purge_learning(cid: str) -> None:
        """Every learning record of a deleted child (conversations/safety events are purged by lifecycle)."""
        planners.pop(cid, None)
        plan_states.pop(cid, None)
        tracings.pop(cid, None)
        for k in [k for k in attempts if k.startswith(cid + ":")]:
            del attempts[k]
        for eid in [i for i, e in exam.exams.items() if e.profile_id == cid]:
            del exam.exams[eid]
        for d in (reports._assigned, reports._done):
            for k in [k for k in d if k[0] == cid]:
                del d[k]
        reports._reviews.pop(cid, None)
        st.settings.pop(cid, None)
        for k in [k for k in st._days if k[0] == cid]:
            del st._days[k]
        for rid in [r.id for r in st.requests.values() if r.child_id == cid]:
            del st.requests[rid]
        usage.purge_child(cid)
        push.purge_child(cid)

    life.child_purge_hooks.append(purge_learning)

    # ---- persistence (PostgreSQL when configured) ----------------------------------------
    register_all(sync, acc=acc, planners=planners, plan_states=plan_states, tracings=tracings, attempts=attempts,
                 sdb=sdb, tol=tol, exam=exam, formats=formats, reports=reports, st=st, bot=bot, conv=conv,
                 usage=usage, bill=bill, refunds=refunds, life=life, center=push)
    restored = sync.load()
    rebuild_indexes(acc)
    if sync.persistent:
        log.info("state restored from PostgreSQL: %d rows", sum(restored.values()))

    def persist() -> int:
        return sync.flush()

    def learning_records(cid: str, thread_ids: Optional[List[str]] = None) -> int:
        """Remaining learning rows for a child (deletion check): memory + every PostgreSQL table, including
        the LangGraph checkpoints of the child's former threads."""
        n = int(cid in planners) + int(cid in plan_states) + int(cid in tracings)
        n += sum(k.startswith(cid + ":") for k in attempts)
        n += sum(e.profile_id == cid for e in exam.exams.values())
        n += sum(k[0] == cid for k in reports._assigned) + sum(k[0] == cid for k in reports._done)
        n += len(reports._reviews.get(cid, [])) + sum(k[0] == cid for k in st._days)
        n += len(conv.meta.for_child(cid)) + sum(e.child_id == cid for e in bot.events)
        n += usage.counters.rows_for_child(cid) + sum(e.child_id == cid for e in usage.paid_events)
        if sync.persistent:
            n += sum(sync.rows_for_child(cid).values())
        n += conv.checkpointer.rows_for_threads(thread_ids or [])
        return n

    # ---- auth helpers ----------------------------------------------------------------
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

    def parent_child(auth: Optional[str], child_id: str):
        """Parent-only routes: a valid session that owns the child."""
        pid = pid_from(auth)
        c = acc.children.get(child_id)
        if c is None or c.parent_id != pid:
            raise HTTPException(404, {"code": "not_found"})
        return pid, c

    def admin(tok: Optional[str]) -> None:
        import hmac

        if not admin_token or not hmac.compare_digest(tok or "", admin_token):
            raise HTTPException(403, {"code": "admin_only"})

    def unlocked(cid: str) -> None:
        """Screen-time lock: child features stop once the parent's limit is reached (exam excepted)."""
        s = st.status(cid)
        if s["locked"]:
            raise HTTPException(423, {"code": "app_locked", "reason": s["reason"]})

    def guard(fn):
        try:
            return fn()
        except AccountError as e:
            code = {"not_found": 404, "forbidden": 403, "chatbot_disabled_no_ai_consent": 403}.get(e.code, 422)
            raise HTTPException(code, {"code": e.code, "message": str(e)})
        except ScreenTimeError as e:
            raise HTTPException(422 if e.code == "settings_invalid" else 403, {"code": e.code, "message": str(e)})
        except PlanError as e:
            raise HTTPException(422, {"code": "plan_invalid", "message": str(e)})

    def exam_info(b: str, g: str) -> dict:
        status = F.exam_status(formats, pending, b, g)
        out = {"status": status, "message": F.FORMAT_PENDING_MESSAGE if status == "format_pending" else None}
        f = formats.get((b, g))
        if f is not None:  # official format summary for the parent's grade picker
            out.update(questions=f.question_count, minutes=f.minutes, total_score=f.total_score,
                       pass_score=f.pass_score, pass_questions=f.pass_questions,
                       points_per_question=f.points_per_question, type_mix=f.type_mix, notice=f.notice,
                       all_multiple_choice=f.all_choice)
        return out

    def exam_view(e) -> dict:
        v = exam.public_view(e)
        v.update(minutes=e.fmt.minutes, pass_score=e.fmt.pass_score, total_score=e.fmt.total_score,
                 remaining_seconds=0 if e.submitted else max(0, int((e.deadline - clock()).total_seconds())),
                 answers=dict(e.answers), submitted=e.submitted, result=e.result if e.submitted else None)
        return v

    def thread_json(m) -> dict:
        return {"thread_id": m.thread_id, "title": m.title, "created_at": m.created_at.isoformat(),
                "last_message_at": m.last_message_at.isoformat()}

    # ---- background jobs -----------------------------------------------------------
    def run_jobs() -> dict:
        out = {"dormancy": life.run_dormancy_sweep(), "weekly_summary": reports.send_weekly_summaries(),
               "billing": bill.run_due()}
        return out

    closed = threading.Event()

    def close() -> None:
        """Release DB connections / worker threads (tests create many apps). Idempotent."""
        if closed.is_set():
            return
        closed.set()
        try:
            push.wait_idle()
            persist()
        finally:
            if push_pool:
                push_pool.shutdown(wait=True)
            for res in (sync, usage.counters, conv.checkpointer, conv.meta):
                fn = getattr(res, "close", None)
                if fn:
                    try:
                        fn()
                    except Exception:  # noqa: BLE001
                        log.warning("close failed for %s", type(res).__name__)

    @asynccontextmanager
    async def lifespan(_app):
        stop = threading.Event()
        if scheduler:
            def loop():
                while not stop.wait(SCHEDULER_INTERVAL_SECONDS):
                    try:
                        run_jobs()
                        persist()
                    except Exception:  # noqa: BLE001
                        log.exception("scheduled jobs failed")
            threading.Thread(target=loop, daemon=True, name="hanja-jobs").start()
        yield
        stop.set()
        close()

    app = FastAPI(title="hanja-tutor", lifespan=lifespan)
    if sync.persistent:
        app.add_middleware(PersistMiddleware, flush=persist)
    api = APIRouter(prefix="/api")
    api.include_router(accounts_router(acc, login=life.login_parent))
    api.include_router(billing_router(bill, parent_of_session))
    api.include_router(refunds_router(refunds, parent_of_session, admin_token))

    @api.get("/health")
    def health():
        fast = llm_router.for_task("chat")
        return {"ok": True, "llm": {"chat": type(fast).__name__,
                                    "chat_model": getattr(fast, "model_id", None),
                                    "question_generation_model": getattr(llm_router.for_task("question_generation"), "model_id", None)},
                "dictionary": "urimalsaem" if dictionary else None,
                "conversation_store": "postgres" if db_url else "memory",
                "state_store": "postgres" if sync.persistent else "memory",
                "web_push": push.push_enabled}

    if not production:
        @api.post("/dev/verify")
        def dev_verify(body: dict):
            return {"verification_token": ver.complete_verification(str(body.get("ci", "ci-dev")))}

        @api.post("/dev/clock")
        def dev_clock(body: ClockIn):
            if not isinstance(clock, Clock):
                raise HTTPException(409, {"code": "clock_not_shiftable"})
            clock.offset = timedelta(0) if body.reset else clock.offset + timedelta(minutes=body.advance_minutes)
            return {"now": clock().isoformat()}

    @api.get("/catalog")
    def catalog():
        return [{"body": b, "name": BODY_NAMES.get(b, b), "grades": g,
                 "exam": {x: exam_info(b, x) for x in g}} for b, g in bodies.items()]

    # ---- parent: child target / plan --------------------------------------------------
    @api.put("/children/{child_id}/target")
    def set_target(child_id: str, body: TargetIn, authorization: Optional[str] = Header(None)):
        c = child_for(authorization, child_id)
        if body.grade not in bodies.get(body.body, []):
            raise HTTPException(422, {"code": "unsupported_grade"})
        c.extra["target"] = (body.body, body.grade)
        planners.pop(child_id, None)
        plan_states.pop(child_id, None)
        return {"body": body.body, "grade": body.grade, "exam": exam_info(body.body, body.grade)}

    def plan_json(cid: str) -> dict:
        p = planner_of(cid)
        plan = p.plan_for_today()
        sched = p.projected_schedule()
        return {"exam_date": p.exam_date.isoformat() if p.exam_date else None,
                "daily_count": p.default_override, "today_quota": plan.quota, "days_left": plan.days_left,
                "feasible": plan.feasible, "remaining": len(p.remaining()), "overridden": plan.overridden,
                "schedule": [{"date": d.isoformat(), "count": n} for d, n in list(sched.items())[:14]]}

    @api.get("/children/{child_id}/plan")
    def get_plan(child_id: str, authorization: Optional[str] = Header(None)):
        _, c = parent_child(authorization, child_id)
        if not c.extra.get("target"):
            raise HTTPException(409, {"code": "no_target"})
        return plan_json(child_id)

    @api.put("/children/{child_id}/plan")
    def put_plan(child_id: str, body: PlanIn, authorization: Optional[str] = Header(None)):
        _, c = parent_child(authorization, child_id)
        if not c.extra.get("target"):
            raise HTTPException(409, {"code": "no_target"})
        ed = None
        if body.exam_date:
            try:
                ed = date.fromisoformat(body.exam_date)
            except ValueError:
                raise HTTPException(422, {"code": "plan_invalid", "message": "exam_date must be YYYY-MM-DD"})
            if ed < kst_today(clock):
                raise HTTPException(422, {"code": "plan_invalid", "message": "exam date is in the past"})
        p = planner_of(child_id)
        guard(lambda: p.set_daily_override(body.daily_count))
        p.set_exam_date(ed)
        c.extra["exam_date"] = ed.isoformat() if ed else None
        c.extra["daily_count"] = body.daily_count
        return plan_json(child_id)

    # ---- child: today / review ------------------------------------------------------
    @api.get("/children/{child_id}/today")
    def today(child_id: str):
        c = child_for(None, child_id)
        if not c.extra.get("target"):
            raise HTTPException(409, {"code": "no_target"})
        unlocked(child_id)
        s = planner_of(child_id).todays_session()
        reports.start_day(child_id)
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
        unlocked(child_id)
        char = nfc(char)
        try:
            reports.complete_new(child_id, char)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(422, {"code": "cannot_complete", "message": str(e)})
        return {"ok": True}

    @api.post("/children/{child_id}/reviews/{char}")
    def review(child_id: str, char: str, body: ReviewIn):
        child_for(None, child_id)
        unlocked(child_id)
        char = nfc(char)
        try:
            reports.answer_review(child_id, char, body.correct)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(422, {"code": "cannot_review", "message": str(e)})
        return {"ok": True}

    # ---- tracing ------------------------------------------------------------
    @api.get("/tracing/tolerance")
    def tracing_tolerance():
        return tol.as_dict()

    @api.post("/children/{child_id}/tracing/{char}/start")
    def trace_start(child_id: str, char: str):
        child_for(None, child_id)
        unlocked(child_id)
        char = nfc(char)
        r = start_attempt(char, sdb, tol)
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
        if exam._active(child_id) is None:  # resuming is always allowed; a new exam needs unlocked time
            unlocked(child_id)
        try:
            e = exam.start(child_id, t[0], t[1], body.seed)
        except LimitReached as e2:
            raise HTTPException(429, {"code": "limit_reached", "resets_at": e2.resets_at.isoformat()})
        except FormatPending:
            raise HTTPException(409, {"code": "exam_format_pending", "message": F.FORMAT_PENDING_MESSAGE})
        except (ExamError, Q.ExamUnavailable) as e2:
            raise HTTPException(409, {"code": "exam_error", "message": str(e2)})
        return exam_view(e)

    @api.get("/children/{child_id}/exams/active")
    def exam_active(child_id: str):
        child_for(None, child_id)
        e = exam._active(child_id)
        if e is None:
            raise HTTPException(404, {"code": "no_active_exam"})
        return exam_view(e)

    @api.get("/exams/{exam_id}")
    def exam_get(exam_id: str):
        try:
            e = exam._get(exam_id)  # applies the wall-clock deadline (auto-submit)
        except ExamError:
            raise HTTPException(404, {"code": "not_found"})
        return exam_view(e)

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

    # ---- chatbot + conversations (child) ---------------------------------------------
    @api.post("/children/{child_id}/chat")
    def chat(child_id: str, body: ChatIn):
        child_for(None, child_id)
        unlocked(child_id)
        tid, r = guard(lambda: conv.send(child_id, body.message, body.thread_id))
        return {"status": r.status, "reply": r.reply, "locked": r.locked_today, "thread_id": tid,
                "sources": r.sources}

    @api.post("/children/{child_id}/chat/stream")
    def chat_stream(child_id: str, body: ChatIn):
        """SSE: thread -> token* -> done | error (error carries retry=True; the client shows a retry button)."""
        child_for(None, child_id)
        unlocked(child_id)
        guard(lambda: acc.require_chatbot(child_id))
        if body.thread_id:
            guard(lambda: conv._owned_thread(child_id, body.thread_id))
        events = conv.send_stream(child_id, body.message, body.thread_id)

        def frames():
            try:
                for ev in events:
                    yield sse(ev)
            except Exception:  # noqa: BLE001 - the client always gets a retryable error frame
                log.exception("chat stream failed")
                yield sse({"type": "error", "message": "지금은 답을 만들지 못했어요. 조금 있다가 다시 물어봐 주세요.",
                           "retry": True, "status": "error"})

        return StreamingResponse(frames(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @api.get("/children/{child_id}/threads")
    def threads(child_id: str):
        child_for(None, child_id)
        return [thread_json(m) for m in guard(lambda: conv.list_threads(child_id))]

    @api.post("/children/{child_id}/threads", status_code=201)
    def new_thread(child_id: str):
        child_for(None, child_id)
        return thread_json(guard(lambda: conv.create_thread(child_id)))

    @api.get("/children/{child_id}/threads/{thread_id}")
    def thread_messages(child_id: str, thread_id: str):
        child_for(None, child_id)
        return {"thread_id": thread_id, "messages": guard(lambda: conv.get_messages(child_id, thread_id))}

    @api.delete("/children/{child_id}/threads/{thread_id}")
    def delete_thread(child_id: str, thread_id: str):
        child_for(None, child_id)
        guard(lambda: conv.delete_thread_by_child(child_id, thread_id))
        return {"ok": True}

    # ---- parent: conversation viewing / safety events (free, unlimited) ----------------
    @api.get("/children/{child_id}/conversations")
    def parent_threads(child_id: str, authorization: Optional[str] = Header(None)):
        pid, _ = parent_child(authorization, child_id)
        return [thread_json(m) for m in guard(lambda: reports.conversations_list(pid, child_id))]

    @api.get("/children/{child_id}/conversations/{thread_id}")
    def parent_thread(child_id: str, thread_id: str, authorization: Optional[str] = Header(None)):
        pid, _ = parent_child(authorization, child_id)
        return {"thread_id": thread_id,
                "messages": guard(lambda: reports.conversation_messages(pid, child_id, thread_id))}

    @api.delete("/children/{child_id}/conversations/{thread_id}")
    def parent_delete_thread(child_id: str, thread_id: str, authorization: Optional[str] = Header(None)):
        pid, _ = parent_child(authorization, child_id)
        guard(lambda: conv.delete_thread_by_parent(pid, child_id, thread_id))
        return {"ok": True}

    @api.get("/children/{child_id}/safety-events")
    def safety_events(child_id: str, authorization: Optional[str] = Header(None)):
        pid, _ = parent_child(authorization, child_id)
        return [{"id": e.id, "category": e.category, "source": e.source, "at": e.at.isoformat(),
                 "thread_count": e.thread_count, "parent_notified": e.parent_notified}
                for e in guard(lambda: bot.parent_safety_view(pid, child_id))]

    # ---- reports ------------------------------------------------------------------
    @api.get("/children/{child_id}/report/basic")
    def report_basic(child_id: str, authorization: Optional[str] = Header(None)):
        pid = pid_from(authorization)
        return guard(lambda: reports.basic_report(pid, child_id))

    @api.get("/children/{child_id}/report/detailed")
    def report_detailed(child_id: str, authorization: Optional[str] = Header(None)):
        pid = pid_from(authorization)
        try:
            return guard(lambda: reports.detailed_report(pid, child_id))
        except SubscriptionRequired:
            raise HTTPException(402, {"code": "subscription_required"})

    @api.get("/children/{child_id}/usage")
    def usage_view(child_id: str):
        child_for(None, child_id)
        return {"chatbot": usage.snapshot(child_id, CHATBOT), "mockexam": usage.snapshot(child_id, MOCKEXAM)}

    @api.get("/children")
    def children(authorization: Optional[str] = Header(None)):
        pid = pid_from(authorization)
        return [{"id": c.id, "nickname": c.nickname, "ai_consent": c.ai_consent, "locked": c.locked,
                 "target": list(c.extra.get("target") or []), "exam_date": c.extra.get("exam_date")}
                for c in acc.children_of(pid)]

    # ---- screen time (safety: never paywalled) ------------------------------------------
    def st_json(cid: str) -> dict:
        s = st.settings.get(cid)
        return {"daily_minutes": s.daily_minutes if s else None,
                "windows": [[f"{w.start_min // 60:02d}:{w.start_min % 60:02d}",
                             "24:00" if w.end_min == 1440 else f"{w.end_min // 60:02d}:{w.end_min % 60:02d}"]
                            for w in (s.windows if s else [])],
                "status": st.status(cid)}

    @api.get("/children/{child_id}/screentime")
    def get_screentime(child_id: str, authorization: Optional[str] = Header(None)):
        parent_child(authorization, child_id)
        return st_json(child_id)

    @api.put("/children/{child_id}/screentime")
    def put_screentime(child_id: str, body: ScreenTimeIn, authorization: Optional[str] = Header(None)):
        pid, _ = parent_child(authorization, child_id)
        guard(lambda: st.set_limits(pid, child_id, body.daily_minutes, [tuple(w) for w in body.windows]))
        return st_json(child_id)

    @api.put("/parents/me/pin")
    def set_parent_pin(body: ParentPinIn, authorization: Optional[str] = Header(None)):
        pid = pid_from(authorization)
        guard(lambda: st.set_parent_pin(pid, body.pin))
        return {"ok": True}

    @api.post("/children/{child_id}/heartbeat")
    def heartbeat(child_id: str):
        child_for(None, child_id)
        return st.heartbeat(child_id)

    @api.get("/children/{child_id}/screentime/status")
    def st_status(child_id: str):
        child_for(None, child_id)
        return st.status(child_id)

    @api.post("/children/{child_id}/screentime/extra")
    def extra_time(child_id: str, body: ExtraTimeIn):
        """Parent types their PIN on the child's device to grant extra minutes."""
        c = child_for(None, child_id)
        return guard(lambda: st.grant_extra_time(c.parent_id, child_id, body.parent_pin, body.minutes))

    @api.post("/children/{child_id}/extension-requests", status_code=201)
    def ext_request(child_id: str, body: ExtensionIn):
        child_for(None, child_id)
        r = guard(lambda: st.request_extension(child_id, body.minutes))
        return {"id": r.id, "status": r.status, "minutes": r.minutes}

    @api.get("/children/{child_id}/extension-requests/{request_id}")
    def ext_status(child_id: str, request_id: str):
        child_for(None, child_id)
        return {"id": request_id, "status": guard(lambda: st.request_status(child_id, request_id))}

    @api.get("/parents/me/extension-requests")
    def ext_pending(authorization: Optional[str] = Header(None)):
        pid = pid_from(authorization)
        return [{"id": r.id, "child_id": r.child_id, "nickname": acc.children[r.child_id].nickname,
                 "minutes": r.minutes, "created_at": r.created_at.isoformat()} for r in st.pending_requests(pid)]

    @api.post("/parents/me/extension-requests/{request_id}")
    def ext_resolve(request_id: str, body: ResolveIn, authorization: Optional[str] = Header(None)):
        pid = pid_from(authorization)
        r = guard(lambda: st.resolve_request(pid, request_id, body.approve, body.minutes))
        return {"id": r.id, "status": r.status}

    @api.get("/parents/me/notifications")
    def notifications(authorization: Optional[str] = Header(None)):
        """Parent push inbox (the PWA polls it and shows system notifications)."""
        pid = pid_from(authorization)
        return [{"seq": i.seq, "id": i.id, "kind": i.kind, "payload": i.payload, "at": i.created_at.isoformat(),
                 "push": i.push, "email": i.email} for i in push.inbox_for(pid)]

    # ---- Web Push (VAPID) subscriptions: parent accounts only ----------------------------
    @api.get("/push/config")
    def push_config():
        return {"enabled": push.push_enabled, "public_key": vapid.public_key if vapid else None}

    @api.get("/parents/me/push-subscriptions")
    def push_subs(authorization: Optional[str] = Header(None)):
        pid = pid_from(authorization)
        return [{"endpoint_origin": "/".join(s.endpoint.split("/")[:3]), "created_at": s.created_at.isoformat(),
                 "last_status": s.last_status} for s in push.subscriptions_of(pid)]

    @api.post("/parents/me/push-subscriptions", status_code=201)
    def push_subscribe(body: PushSubscriptionIn, authorization: Optional[str] = Header(None)):
        pid = pid_from(authorization)
        if not push.push_enabled:
            raise HTTPException(409, {"code": "push_not_configured"})
        try:
            push.subscribe(pid, body.endpoint, body.keys.p256dh, body.keys.auth)
        except InvalidSubscription as e:
            raise HTTPException(422, {"code": e.code, "message": str(e)})
        return {"ok": True, "subscriptions": len(push.subscriptions_of(pid))}

    @api.delete("/parents/me/push-subscriptions")
    def push_unsubscribe(body: PushEndpointIn, authorization: Optional[str] = Header(None)):
        pid = pid_from(authorization)
        return {"ok": push.unsubscribe(pid, body.endpoint)}

    @api.post("/parents/me/push-test")
    def push_test(authorization: Optional[str] = Header(None)):
        pid = pid_from(authorization)
        item = push.notify(pid, "push_test", {"message": "이 기기에서 한자 튜터 알림을 받을 수 있어요."})
        push.wait_idle()
        return {"push": item.push, "email": item.email}

    # ---- deletion / dormancy ------------------------------------------------------------
    @api.delete("/children/{child_id}")
    def delete_child(child_id: str, authorization: Optional[str] = Header(None)):
        pid, _ = parent_child(authorization, child_id)
        tids = [m.thread_id for m in conv.meta.for_child(child_id)]
        guard(lambda: life.delete_child(pid, child_id))
        persist()
        sync.backend.delete_child_rows(child_id)
        return {"ok": True, "remaining_learning_records": learning_records(child_id, tids)}

    @api.delete("/parents/me")
    def delete_account(authorization: Optional[str] = Header(None)):
        pid = pid_from(authorization)
        kids = [c.id for c in acc.children_of(pid)]
        tids = {k: [m.thread_id for m in conv.meta.for_child(k)] for k in kids}
        bill.subs.pop(pid, None)  # no further renewals for a deleted account
        guard(lambda: life.delete_account(pid))
        push.purge_parent(pid)
        persist()  # one transaction: learning rows deleted, payments -> payment_archive
        for k in kids:
            sync.backend.delete_child_rows(k)
        return {"ok": True, "remaining_learning_records": sum(learning_records(k, tids[k]) for k in kids),
                "archived_payments": sum(1 for r in life.archive.rows if r.record.parent_id == pid)}

    # ---- operator jobs (cron / scheduler) -------------------------------------------------
    @api.post("/admin/jobs/run")
    def admin_jobs(x_admin_token: Optional[str] = Header(None)):
        admin(x_admin_token)
        out = run_jobs()
        push.wait_idle()
        return out

    @api.post("/admin/jobs/weekly-summary")
    def admin_weekly(x_admin_token: Optional[str] = Header(None)):
        admin(x_admin_token)
        return {"sent": reports.send_weekly_summaries()}

    @api.post("/admin/jobs/dormancy")
    def admin_dormancy(x_admin_token: Optional[str] = Header(None)):
        admin(x_admin_token)
        return life.run_dormancy_sweep()

    app.include_router(api)
    app.state.services = dict(acc=acc, usage=usage, bill=bill, refunds=refunds, exam=exam, gateway=gw,
                              llm_router=llm_router, store=store, strokes=sdb, bot=bot, reports=reports,
                              conv=conv, life=life, screentime=st, planners=planners, push=push,
                              learning_records=learning_records, clock=clock, dictionary=dictionary, tol=tol,
                              sync=sync, persist=persist, mail=mail)
    app.state.close = close
    return app


_APP: Optional[FastAPI] = None


def __getattr__(name: str):
    """``uvicorn app.main:app`` builds the server app lazily, so importing this module (tests) never
    reads backend/.env or starts the scheduler."""
    global _APP
    if name == "app":
        if _APP is None:
            _APP = create_app(scheduler=True)
        return _APP
    raise AttributeError(name)
