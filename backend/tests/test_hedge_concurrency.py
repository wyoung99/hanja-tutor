"""Latency work for AC9 (hermetic, fake LLM with injected delays):

* concurrent safety gate in the streaming path: the LLM classifier overlaps answer generation, but no answer
  text is emitted before the classifier allows the message; blocked / redirected messages behave exactly as
  in the sequential path;
* hedged requests (llm.hedge): one duplicate request after the hedge delay, faster first chunk wins, loser
  cancelled, at most one hedge, disabled when 0.
"""
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from accounts.service import AccountService, FakeIdentityVerifier  # noqa: E402
from chatbot.service import ChatbotService, RecordingNotifier  # noqa: E402
from chatbot_cases import CASES  # noqa: E402
from llm import (FAST, REASONING, FakeProvider, HedgedProvider, LLMError, LLMRouter,  # noqa: E402
                 hedge_after_seconds)
from test_chatbot import Clock, KST, make_kb  # noqa: E402
from usage import CHATBOT, UsageLimitConfig, UsageService  # noqa: E402
from datetime import datetime  # noqa: E402

ANSWER = "學은 배우다예요. 쉬운 말이에요. 같이 해요."
Q = "學이 뭐예요?"


def world(label="ON_TOPIC", cls_delay=0.0, ans_delay=0.0, chunk_delay=0.0, limit=5, answer=ANSWER):
    ver = FakeIdentityVerifier()
    acc = AccountService(ver)
    p = acc.signup_parent(ver.complete_verification("ci1"), "p@x.kr", True, True, True)
    child = acc.create_child(p.id, "별이", "1234", ai_consent=True)
    child.extra["target"] = ("b", "8")
    clock = Clock(datetime(2026, 11, 5, 10, 0, tzinfo=KST))
    notifier = RecordingNotifier()
    usage = UsageService(UsageLimitConfig.from_mapping({CHATBOT: limit}), clock,
                         parent_of=lambda cid: (p.id, "별이"), notifier=notifier)
    cls_llm = FakeProvider(label if callable(label) else (lambda s, m: label), first_delay=cls_delay)
    ans_llm = FakeProvider(lambda s, m: answer, first_delay=ans_delay, chunk_delay=chunk_delay)
    bot = ChatbotService(acc, make_kb(), ans_llm, None, notifier, clock, usage=usage, classifier_llm=cls_llm)
    return bot, child, usage, notifier, cls_llm, ans_llm


def timed(gen):
    """[(seconds since start, event)] for a stream."""
    t0 = time.monotonic()
    return [(time.monotonic() - t0, ev) for ev in gen]


def wait_for(cond, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


# ---- concurrent gate ---------------------------------------------------------
def test_answer_starts_before_classifier_returns_but_nothing_is_emitted_before_allow():
    allowed_at = []

    def label(s, m):
        allowed_at.append(time.monotonic())
        return "ON_TOPIC"

    bot, child, usage, _, cls_llm, ans_llm = world(label=label, cls_delay=0.4)
    t0 = time.monotonic()
    first_token_at = None
    for ev in bot.ask_stream(child.id, "t", Q):
        if ev["type"] == "token" and first_token_at is None:
            first_token_at = time.monotonic()
            assert len(ans_llm.calls) == 1  # the answer stream was already running
    assert first_token_at is not None and first_token_at >= allowed_at[0] >= t0 + 0.38
    assert usage.snapshot(child.id, CHATBOT)["used"] == 1


def test_first_token_is_max_of_classifier_and_first_sentence_not_the_sum():
    bot, child, *_ = world(cls_delay=0.5, ans_delay=0.5)
    ev = timed(bot.ask_stream(child.id, "t", Q))
    first = next(t for t, e in ev if e["type"] == "token")
    assert 0.45 <= first < 0.85, first  # sequential would be >= 1.0s
    assert ev[-1][1]["status"] == "answered"


@pytest.mark.parametrize("label,category", [("HARMFUL", "violence"), ("PII", "pii")])
def test_classifier_block_emits_no_answer_text_cancels_stream_and_records_same_outcome(label, category):
    bot, child, usage, notifier, cls_llm, ans_llm = world(label=label, cls_delay=0.3, chunk_delay=0.05,
                                                          answer=ANSWER * 3)
    msg = "그 애를 없애 버리고 싶어 學"  # passes the rules; only the classifier blocks it
    ev = list(bot.ask_stream(child.id, "t1", msg))
    assert [e["type"] for e in ev] == ["done"]
    assert ev[0]["status"] == "blocked" and ev[0]["category"] == category and "學은" not in ev[0]["reply"]
    assert len(bot.events) == 1 and bot.events[0].source == "llm" and bot.events[0].category == category
    assert len(notifier.sent) == 1 and notifier.sent[0][1] == "safety_event"
    assert msg not in repr(bot.events) and msg not in repr(notifier.sent)  # raw text not stored
    assert usage.snapshot(child.id, CHATBOT)["used"] == 0
    assert wait_for(lambda: ans_llm.cancelled == [0])  # the speculative answer stream was closed


def test_classifier_blocks_three_times_locks_for_the_day_like_sequential_path():
    bot, child, usage, notifier, *_ = world(label="HARMFUL", cls_delay=0.05)
    outs = [list(bot.ask_stream(child.id, "t1", "그 애를 없애 버리고 싶어"))[-1] for _ in range(3)]
    assert [o["status"] for o in outs] == ["blocked"] * 3 and outs[-1]["locked_today"]
    assert list(bot.ask_stream(child.id, "t2", Q))[-1]["status"] == "locked"
    assert usage.snapshot(child.id, CHATBOT)["used"] == 0


def test_classifier_redirect_emits_no_answer_text_and_is_free():
    bot, child, usage, notifier, cls_llm, ans_llm = world(label="OFF_TOPIC", cls_delay=0.2)
    ev = list(bot.ask_stream(child.id, "t", "그냥 이야기하자"))
    assert [e["type"] for e in ev] == ["done"] and ev[0]["status"] == "redirected"
    assert "도와줄 수 없" in ev[0]["reply"] and bot.events == [] and notifier.sent == []
    assert usage.snapshot(child.id, CHATBOT)["used"] == 0


def test_classifier_failure_fails_closed_to_redirect_without_answer_text():
    def boom(s, m):
        raise LLMError("down")

    bot, child, usage, *_ = world(label=boom, cls_delay=0.1)
    ev = list(bot.ask_stream(child.id, "t", "그냥 이야기하자"))
    assert [e["type"] for e in ev] == ["done"] and ev[0]["status"] == "redirected"
    assert usage.snapshot(child.id, CHATBOT)["used"] == 0


def test_classifier_timeout_returns_retryable_error_and_shows_no_answer():
    bot, child, usage, *_ = world(cls_delay=1.0)
    t0 = time.monotonic()
    ev = list(bot.ask_stream(child.id, "t", Q, timeout=0.3))
    assert time.monotonic() - t0 < 0.8
    assert [e["type"] for e in ev] == ["error"] and ev[0]["retry"]
    assert usage.snapshot(child.id, CHATBOT)["used"] == 0


def test_exhausted_usage_does_not_speculate_and_block_still_wins():
    bot, child, usage, notifier, cls_llm, ans_llm = world(limit=1)
    assert list(bot.ask_stream(child.id, "t", Q))[-1]["status"] == "answered"
    assert len(ans_llm.calls) == 1
    ev = list(bot.ask_stream(child.id, "t", Q))
    assert [e["type"] for e in ev] == ["done"] and ev[0]["status"] == "locked"
    assert len(ans_llm.calls) == 1  # no answer call for a child who cannot be answered today
    bot2, child2, usage2, notifier2, *_ = world(label="HARMFUL", limit=0)
    assert list(bot2.ask_stream(child2.id, "t", "그 애를 없애 버리고 싶어"))[-1]["status"] == "blocked"
    assert len(notifier2.sent) == 1  # the safety event is recorded exactly as before


def test_failed_answer_after_allow_is_not_counted():
    bot, child, usage, *_ = world(cls_delay=0.1)
    bot.llm.handler = lambda s, m: (_ for _ in ()).throw(LLMError("down"))
    ev = list(bot.ask_stream(child.id, "t", Q))
    assert ev[-1]["type"] == "error" and usage.snapshot(child.id, CHATBOT)["used"] == 0


def test_client_abort_after_first_token_cancels_answer_and_is_free():
    bot, child, usage, notifier, cls_llm, ans_llm = world(cls_delay=0.3, chunk_delay=0.05, answer=ANSWER * 3)
    g = bot.ask_stream(child.id, "t", Q)
    next(g)  # first token (after allow)
    g.close()
    assert usage.snapshot(child.id, CHATBOT)["used"] == 0
    assert wait_for(lambda: ans_llm.cancelled == [0])


@pytest.mark.parametrize("message,expected", CASES)
def test_labelled_guard_set_through_concurrent_stream_path(message, expected):
    """The 114-case set behaves as labelled on the streaming path too (fake classifier, as in test_chatbot)."""
    ver = FakeIdentityVerifier()
    acc = AccountService(ver)
    p = acc.signup_parent(ver.complete_verification("ci1"), "p@x.kr", True, True, True)
    child = acc.create_child(p.id, "별이", "1234", ai_consent=True)
    child.extra["target"] = ("b", "8")
    llm = FakeProvider(lambda s, m: "OFF_TOPIC" if "분류기" in s else "답변이에요.")
    notifier = RecordingNotifier()
    svc = ChatbotService(acc, make_kb(), llm, None, notifier, Clock(datetime(2026, 11, 5, 10, tzinfo=KST)))
    ev = list(svc.ask_stream(child.id, "t1", message))
    assert [e["type"] for e in ev] == ["done"]  # no answer text ever streamed
    r = ev[0]
    if expected == "redirect":
        assert r["status"] == "redirected" and "도와줄 수 없" in r["reply"] and "學校" in r["reply"]
        assert svc.events == []
    else:
        assert r["status"] == "blocked" and len(svc.events) == 1 and len(notifier.sent) == 1
        assert message not in r["reply"]


# ---- hedged requests -----------------------------------------------------------
def _by_thread(s, m):
    """Reply text tells which attempt answered (hedge threads are named llm-hedge-<name>-<k>)."""
    return "hedge reply." if threading.current_thread().name.endswith("-1") else "primary reply."


def test_no_hedge_when_first_chunk_arrives_before_the_delay():
    fake = FakeProvider(_by_thread, first_delay=0.05)
    h = HedgedProvider(fake, 0.3)
    assert "".join(h.stream("s", [])) == "primary reply."
    assert h.complete("s", []) == "primary reply."
    assert len(fake.calls) == 2 and h.stats.snapshot() == {"calls": 2, "hedged": 0, "hedge_won": 0}


def test_hedge_fires_only_after_the_delay_and_faster_hedge_wins_loser_cancelled():
    fake = FakeProvider(_by_thread, first_delay=[0.8, 0.05], chunk_delay=0.01)
    h = HedgedProvider(fake, 0.2)
    t0 = time.monotonic()
    g = h.stream("s", [])
    first = next(g)
    elapsed = time.monotonic() - t0
    assert 0.2 <= elapsed < 0.6, elapsed  # hedge started at 0.2s, answered 0.05s later
    assert first + "".join(g) == "hedge reply."
    assert h.stats.snapshot() == {"calls": 1, "hedged": 1, "hedge_won": 1}
    assert wait_for(lambda: fake.cancelled == [0])  # the slow primary was closed when it finally yielded
    assert len(fake.calls) == 2


def test_hedge_fires_but_primary_still_wins_and_hedge_is_cancelled():
    fake = FakeProvider(_by_thread, first_delay=[0.3, 1.0], chunk_delay=0.01)
    h = HedgedProvider(fake, 0.1)
    assert "".join(h.stream("s", [])) == "primary reply."
    assert h.stats.snapshot() == {"calls": 1, "hedged": 1, "hedge_won": 0}
    assert wait_for(lambda: fake.cancelled == [1])


def test_at_most_one_hedge_per_call():
    fake = FakeProvider(_by_thread, first_delay=0.5)
    h = HedgedProvider(fake, 0.05)
    assert h.complete("s", []) in ("primary reply.", "hedge reply.")
    time.sleep(0.6)
    assert len(fake.calls) == 2 and h.stats.hedged == 1


def test_hedging_disabled_with_zero():
    fake = FakeProvider(_by_thread, first_delay=0.3)
    h = HedgedProvider(fake, 0)
    assert "".join(h.stream("s", [])) == "primary reply." and h.complete("s", []) == "primary reply."
    time.sleep(0.05)
    assert len(fake.calls) == 2 and h.stats.snapshot() == {"calls": 2, "hedged": 0, "hedge_won": 0}


def test_complete_is_hedged_too_for_the_classifier():
    fake = FakeProvider(lambda s, m: "OFF_TOPIC" if threading.current_thread().name.endswith("-1") else "ON_TOPIC",
                        first_delay=[1.0, 0.0])
    h = HedgedProvider(fake, 0.1)
    t0 = time.monotonic()
    assert h.complete("s", []) == "OFF_TOPIC" and time.monotonic() - t0 < 0.5
    assert h.stats.hedge_won == 1


def test_errors_are_not_retried_before_the_hedge_but_a_live_hedge_can_still_answer():
    def fail_primary(s, m):
        if threading.current_thread().name.endswith("-0"):
            raise LLMError("primary down")
        return "hedge reply."

    fast_fail = HedgedProvider(FakeProvider(fail_primary), 0.5)
    with pytest.raises(LLMError):
        fast_fail.complete("s", [])
    assert fast_fail.stats.hedged == 0  # an error is not slowness: no duplicate request
    slow_fail = HedgedProvider(FakeProvider(fail_primary, first_delay=[0.3, 0.3]), 0.1)
    assert slow_fail.complete("s", []) == "hedge reply."
    both = HedgedProvider(FakeProvider(lambda s, m: (_ for _ in ()).throw(LLMError("x")), first_delay=0.2), 0.05)
    with pytest.raises(LLMError):
        list(both.stream("s", []))


def test_hedged_answer_stream_rescues_a_slow_first_token():
    bot, child, *_ = world()
    bot.llm = HedgedProvider(FakeProvider(lambda s, m: ANSWER, first_delay=[2.0, 0.05]), 0.2, name="chat")
    ev = timed(bot.ask_stream(child.id, "t", Q))
    first = next(t for t, e in ev if e["type"] == "token")
    assert first < 0.8 and ev[-1][1]["status"] == "answered"
    assert bot.llm.stats.hedge_won == 1
    assert bot.llm.calls[0][1][-1]["content"] == Q  # attributes delegate to the wrapped provider


def test_router_hedges_only_haiku_chat_and_classifier_and_env_config():
    fast, reasoning = FakeProvider(), FakeProvider()
    r = LLMRouter({FAST: fast, REASONING: reasoning})
    chat = r.hedged("chat", 1.5)
    assert isinstance(chat, HedgedProvider) and chat.inner is fast and r.hedged("chat", 1.5) is chat
    assert isinstance(r.hedged("safety_classifier", 1.5), HedgedProvider)
    assert r.hedged("question_generation", 1.5) is reasoning  # Sonnet question generation never hedged
    assert r.for_task("chat") is fast  # the plain provider is unchanged
    assert set(r.hedge_stats()) == {"chat", "safety_classifier"}
    assert r.hedge_stats()["chat"] == {"hedge_after_s": 1.5, "calls": 0, "hedged": 0, "hedge_won": 0}
    assert hedge_after_seconds({}) == 1.5
    assert hedge_after_seconds({"LLM_HEDGE_AFTER_S": "0"}) == 0
    assert hedge_after_seconds({"LLM_HEDGE_AFTER_S": "0.8"}) == 0.8
    assert hedge_after_seconds({"LLM_HEDGE_AFTER_S": "abc"}) == 1.5
    assert hedge_after_seconds({"LLM_HEDGE_AFTER_S": "-1"}) == 0

