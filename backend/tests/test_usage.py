import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from accounts.service import AccountService, FakeIdentityVerifier  # noqa: E402
from chatbot.service import ChatbotService, RecordingNotifier  # noqa: E402
from llm import FakeProvider, LLMError  # noqa: E402
from mockexam.service import MockExamService  # noqa: E402
from study.planner import PlanHanja, StudyPlanner  # noqa: E402
from tracing.grader import start_attempt  # noqa: E402
from usage import (  # noqa: E402
    CHATBOT, MOCKEXAM, UNMETERED, FeatureLocked, LimitReached, UsageLimitConfig, UsageService,
    child_notice, next_reset, parent_notice, period_start,
)
from test_chatbot import make_kb  # noqa: E402
from test_mockexam import FORMATS, SDB, HANJA, ASSIGN, BODIES, Clock, correct_answers, make  # noqa: E402

KST = timezone(timedelta(hours=9))
WED = datetime(2026, 11, 4, 10, 0, tzinfo=KST)  # Wednesday


class Paid:
    def __init__(self, paid=True):
        self.paid = paid

    def is_paid(self, child_id):
        return self.paid


def world(limits=None, t=WED, sub=None, handler=None):
    ver = FakeIdentityVerifier()
    acc = AccountService(ver)
    p = acc.signup_parent(ver.complete_verification("ci1"), "p@x.kr", True, True, True)
    a = acc.create_child(p.id, "별이", "1234", ai_consent=True)
    b = acc.create_child(p.id, "달이", "1234", ai_consent=True)
    for c in (a, b):
        c.extra["target"] = ("b", "8")
    clock = Clock(t)
    notifier = RecordingNotifier()
    kw = {"subscription": sub} if sub else {}
    usage = UsageService(UsageLimitConfig.from_mapping(limits), clock,
                         parent_of=lambda cid: (p.id, acc.children[cid].nickname), notifier=notifier, **kw)
    llm = FakeProvider(handler or (lambda s, m: "OFF_TOPIC" if "분류기" in s else "學은 배우다예요. 쉬운 말이에요. 같이 해요."))
    bot = ChatbotService(acc, make_kb(), llm, None, notifier, clock, use_llm_classifier=False, usage=usage)
    return bot, usage, acc, p, a, b, clock, notifier


def ask_n(bot, child, n):
    return [bot.ask(child.id, "t", "學이 뭐예요?").status for _ in range(n)]


# ---- chatbot daily ----------------------------------------------------------
def test_default_config_is_beta_values_and_env_overrides():
    c = UsageLimitConfig.from_mapping()
    assert c.limits == {CHATBOT: 20, MOCKEXAM: 1}
    e = UsageLimitConfig.from_env({"USAGE_CHATBOT_DAILY_LIMIT": "5", "USAGE_MOCKEXAM_WEEKLY_LIMIT": "2"})
    assert e.limits == {CHATBOT: 5, MOCKEXAM: 2}


def test_chatbot_20_per_day_then_locked_until_kst_midnight():
    bot, usage, acc, p, a, b, clock, _ = world()
    st = ask_n(bot, a, 21)
    assert st[:20] == ["answered"] * 20 and st[20] == "locked"
    clock.t = datetime(2026, 11, 4, 23, 59, 59, tzinfo=KST)
    assert bot.ask(a.id, "t", "學이 뭐예요?").status == "locked"
    clock.t = datetime(2026, 11, 5, 0, 0, 0, tzinfo=KST)
    assert bot.ask(a.id, "t", "學이 뭐예요?").status == "answered"


def test_kst_boundary_uses_kst_not_utc():
    utc = timezone.utc
    # 2026-11-04 15:00 UTC == 2026-11-05 00:00 KST : new day already
    assert period_start(CHATBOT, datetime(2026, 11, 4, 15, 0, tzinfo=utc)) == datetime(2026, 11, 5, tzinfo=KST)
    assert period_start(CHATBOT, datetime(2026, 11, 4, 14, 59, tzinfo=utc)) == datetime(2026, 11, 4, tzinfo=KST)
    # Sunday 23:59 KST still previous week; Monday 00:00 KST new week
    assert period_start(MOCKEXAM, datetime(2026, 11, 8, 23, 59, tzinfo=KST)) == datetime(2026, 11, 2, tzinfo=KST)
    assert period_start(MOCKEXAM, datetime(2026, 11, 9, 0, 0, tzinfo=KST)) == datetime(2026, 11, 9, tzinfo=KST)
    assert next_reset(MOCKEXAM, WED) == datetime(2026, 11, 9, tzinfo=KST)


def test_siblings_counted_separately():
    bot, usage, acc, p, a, b, *_ = world({CHATBOT: 3})
    assert ask_n(bot, a, 4)[-1] == "locked"
    assert ask_n(bot, b, 3) == ["answered"] * 3
    assert usage.snapshot(b.id, CHATBOT)["used"] == 3 and usage.is_locked(a.id, CHATBOT)


def test_failed_blocked_redirected_responses_do_not_count():
    fail = {"on": True}

    def handler(s, m):
        if fail["on"]:
            raise LLMError("down")
        return "學은 배우다예요. 쉬운 말이에요. 같이 해요."

    bot, usage, acc, p, a, b, *_ = world({CHATBOT: 2}, handler=handler)
    assert ask_n(bot, a, 3) == ["error"] * 3
    assert usage.snapshot(a.id, CHATBOT)["used"] == 0
    assert bot.ask(a.id, "t", "씨발").status == "blocked"
    assert bot.ask(a.id, "t2", "오늘 날씨 어때?").status in ("redirected", "blocked")
    assert usage.snapshot(a.id, CHATBOT)["used"] == 0
    fail["on"] = False
    assert ask_n(bot, a, 3) == ["answered", "answered", "locked"]


def test_concurrent_devices_never_exceed_limit():
    bot, usage, acc, p, a, b, *_ = world({CHATBOT: 5})
    out, barrier = [], threading.Barrier(16)

    def go():
        barrier.wait()
        try:
            usage.consume(a.id, CHATBOT)
            out.append(1)
        except LimitReached:
            out.append(0)

    ts = [threading.Thread(target=go) for _ in range(16)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(out) == 5 and usage.snapshot(a.id, CHATBOT)["used"] == 5


def test_concurrent_chat_requests_never_exceed_limit():
    bot, usage, acc, p, a, b, *_ = world({CHATBOT: 3})
    res, barrier = [], threading.Barrier(10)

    def go():
        barrier.wait()
        res.append(bot.ask(a.id, "t", "學이 뭐예요?").status)

    ts = [threading.Thread(target=go) for _ in range(10)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert res.count("answered") == 3 and res.count("locked") == 7


# ---- mock exam weekly -------------------------------------------------------
def exam_world(limit=1, t=WED):
    usage = UsageService(UsageLimitConfig.from_mapping({MOCKEXAM: limit}), Clock(t))
    svc, clock = make(clock=usage.clock, charge=usage.exam_charge())
    return svc, usage, clock


def first_fmt():
    body = next(iter(BODIES))
    return body, BODIES[body][0]


def test_one_exam_per_week_resume_and_autosubmit_do_not_charge():
    svc, usage, clock = exam_world()
    body, grade = first_fmt()
    e = svc.start("c1", body, grade)
    assert usage.snapshot("c1", MOCKEXAM)["used"] == 1
    clock.t += timedelta(minutes=1)
    assert svc.start("c1", body, grade) is e  # resume: same exam
    assert usage.snapshot("c1", MOCKEXAM)["used"] == 1
    clock.t = e.deadline + timedelta(seconds=1)  # time out -> auto submit
    assert svc.result(e.exam_id)["auto_submitted"]
    assert usage.snapshot("c1", MOCKEXAM)["used"] == 1
    with pytest.raises(LimitReached):  # new exam same week: locked
        svc.start("c1", body, grade)
    assert usage.snapshot("c1", MOCKEXAM)["used"] == 1


def test_exam_locked_until_monday_midnight_kst_and_sibling_separate():
    svc, usage, clock = exam_world()
    body, grade = first_fmt()
    e = svc.start("c1", body, grade)
    svc.submit(e.exam_id)
    clock.t = datetime(2026, 11, 8, 23, 59, tzinfo=KST)  # Sunday
    with pytest.raises(LimitReached) as ei:
        svc.start("c1", body, grade)
    assert ei.value.resets_at == datetime(2026, 11, 9, tzinfo=KST)
    svc.start("c2", body, grade)  # sibling has own count
    clock.t = datetime(2026, 11, 9, 0, 0, tzinfo=KST)
    svc.start("c1", body, grade)


def test_failed_generation_does_not_charge():
    svc, usage, _ = exam_world()
    with pytest.raises(Exception):
        svc.start("c1", "nobody", "8")
    assert usage.snapshot("c1", MOCKEXAM)["used"] == 0


def test_concurrent_exam_starts_charge_once():
    usage = UsageService(UsageLimitConfig.from_mapping({MOCKEXAM: 1}), Clock(WED))
    ok, barrier = [], threading.Barrier(8)

    def go():
        barrier.wait()
        try:
            usage.consume("c1", MOCKEXAM)
            ok.append(1)
        except LimitReached:
            pass

    ts = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(ok) == 1


# ---- lock scope + messaging -------------------------------------------------
def test_locks_only_affect_metered_features_and_child_sees_no_subscription_prompt():
    bot, usage, acc, p, a, b, clock, notifier = world({CHATBOT: 1, MOCKEXAM: 1})
    ask_n(bot, a, 1)
    usage.consume(a.id, MOCKEXAM)
    r = bot.ask(a.id, "t", "學이 뭐예요?")
    assert r.status == "locked" and "내일 또 만나요" in r.reply
    for word in ("구독", "결제", "유료", "무료", "플랜", "프리미엄"):
        assert word not in r.reply and word not in child_notice(CHATBOT) and word not in child_notice(MOCKEXAM)
    assert usage.is_locked(a.id, CHATBOT) and usage.is_locked(a.id, MOCKEXAM)
    # unmetered features: never locked, never counted
    for f in UNMETERED:
        assert not usage.is_locked(a.id, f)
        with pytest.raises(ValueError):
            usage.consume(a.id, f)
    feats = acc.features(a.id)
    assert feats["todays_hanja"] and feats["review"] and feats["tracing"] and feats["reports"]
    pl = StudyPlanner([PlanHanja(chr(0x4E00 + i), i + 1) for i in range(20)], clock=clock)
    assert pl.todays_session() is not None and pl.due_reviews() == []
    ch = next(iter(SDB))
    assert start_attempt(ch, SDB)["mode"] == "graded"
    assert bot.parent_safety_view(p.id, a.id) == []  # safety/supervision still works
    assert bot.ask(a.id, "t", "씨발").status in ("blocked", "locked")  # input guard path is not paywalled


def test_parent_gets_subscription_prompt_once_per_period_via_push():
    bot, usage, acc, p, a, b, clock, notifier = world({CHATBOT: 1})
    ask_n(bot, a, 3)
    sent = [n for n in notifier.sent if n[1] == "usage_limit_reached"]
    assert len(sent) == 1 and sent[0][0] == p.id
    assert "구독" in sent[0][2]["message"] and sent[0][2]["subscribe_prompt"]
    assert "구독" in parent_notice(CHATBOT, "별이", next_reset(CHATBOT, WED))


# ---- limit changes ----------------------------------------------------------
def test_raise_is_immediate_lower_is_from_next_reset_and_not_retroactive():
    bot, usage, acc, p, a, b, clock, _ = world({CHATBOT: 2})
    assert ask_n(bot, a, 3)[-1] == "locked"
    usage.config.set_limit(CHATBOT, 4, clock())  # raise: immediate
    assert ask_n(bot, a, 3) == ["answered", "answered", "locked"]
    usage.config.set_limit(CHATBOT, 1, clock())  # lower: today still 4
    assert usage.limit(CHATBOT) == 4 and usage.snapshot(b.id, CHATBOT)["limit"] == 4
    assert ask_n(bot, b, 4) == ["answered"] * 4  # still 4 today
    clock.t = datetime(2026, 11, 5, 0, 0, tzinfo=KST)
    assert usage.limit(CHATBOT) == 1
    assert ask_n(bot, a, 2) == ["answered", "locked"]


def test_weekly_lower_applies_next_monday_only():
    usage = UsageService(UsageLimitConfig.from_mapping({MOCKEXAM: 3}), Clock(WED))
    usage.config.set_limit(MOCKEXAM, 1, WED)
    assert usage.limit(MOCKEXAM) == 3
    usage.clock.t = datetime(2026, 11, 8, 23, 59, tzinfo=KST)
    assert usage.limit(MOCKEXAM) == 3
    usage.clock.t = datetime(2026, 11, 9, tzinfo=KST)
    assert usage.limit(MOCKEXAM) == 1


# ---- subscription interface -------------------------------------------------
def test_paid_is_unlimited_and_records_over_free_usage_events():
    bot, usage, acc, p, a, b, clock, _ = world({CHATBOT: 2}, sub=Paid())
    assert ask_n(bot, a, 5) == ["answered"] * 5
    assert not usage.is_locked(a.id, CHATBOT) and usage.snapshot(a.id, CHATBOT)["used"] == 5
    assert len(usage.paid_events) == 3  # 3rd..5th question are beyond the free limit
