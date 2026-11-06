import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chatbot.streaming import FIRST_TOKEN_SLO_SECONDS, LatencyRecorder, sse, stream_ask  # noqa: E402
from llm import FakeProvider, LLMError  # noqa: E402
from test_usage import world  # noqa: E402
from usage import CHATBOT  # noqa: E402

TEXT = "學은 배우다예요. 쉬운 말이에요. 같이 해요."


def run(bot, child, **kw):
    return list(bot.ask_stream(child.id, "t", "學이 뭐예요?", **kw))


def test_streams_tokens_then_done_and_counts_usage():
    bot, usage, acc, p, a, *_ = world({CHATBOT: 5})
    ev = run(bot, a)
    kinds = [e["type"] for e in ev]
    assert kinds[0] == "token" and kinds[-1] == "done" and "error" not in kinds
    assert ev[-1]["status"] == "answered"
    assert usage.snapshot(a.id, CHATBOT)["used"] == 1
    assert sse(ev[0]).startswith("event: token\ndata: ")


def test_first_token_arrives_fast_and_p95_helper():
    bot, usage, acc, p, a, *_ = world()
    r = LatencyRecorder()
    for _ in range(20):
        t0 = time.monotonic()
        stream_ask(bot, a.id, "t", "學이 뭐예요?", None).__next__()
        r.record(time.monotonic() - t0)
    assert r.p95() < FIRST_TOKEN_SLO_SECONDS
    assert LatencyRecorder().p95() is None


def test_llm_error_shows_retry_notice_and_not_counted():
    def boom(s, m):
        raise LLMError("down")

    bot, usage, acc, p, a, *_ = world({CHATBOT: 2}, handler=boom)
    ev = run(bot, a)
    assert ev[-1]["type"] == "error" and ev[-1]["retry"] is True and ev[-1]["message"]
    assert usage.snapshot(a.id, CHATBOT)["used"] == 0


def test_timeout_after_15s_budget_errors_and_not_counted():
    def slow(s, m):
        yield "學은 "
        time.sleep(1.0)
        yield "배우다예요. 쉬운 말이에요. 같이 해요."

    bot, usage, acc, p, a, *_ = world({CHATBOT: 2}, handler=slow)
    t0 = time.monotonic()
    ev = run(bot, a, timeout=0.3)
    assert time.monotonic() - t0 < 0.9
    assert ev[-1]["type"] == "error" and ev[-1]["retry"]
    assert usage.snapshot(a.id, CHATBOT)["used"] == 0
    assert bot.ask_stream.__defaults__[-1] == 15.0  # default budget is 15s


def test_client_abort_is_not_counted():
    bot, usage, acc, p, a, *_ = world({CHATBOT: 2})
    g = bot.ask_stream(a.id, "t", "學이 뭐예요?")
    next(g)
    g.close()
    assert usage.snapshot(a.id, CHATBOT)["used"] == 0


def test_limit_locks_stream_with_child_notice_only():
    bot, usage, acc, p, a, *_ = world({CHATBOT: 1})
    assert run(bot, a)[-1]["status"] == "answered"
    ev = run(bot, a)
    assert [e["type"] for e in ev] == ["done"] and ev[0]["status"] == "locked"
    assert "구독" not in ev[0]["reply"]


def test_blocked_input_streams_block_without_usage():
    bot, usage, acc, p, a, *_ = world({CHATBOT: 2})
    ev = list(bot.ask_stream(a.id, "t", "씨발"))
    assert ev[-1]["status"] == "blocked" and usage.snapshot(a.id, CHATBOT)["used"] == 0
