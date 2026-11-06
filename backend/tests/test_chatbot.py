import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import re

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


_PHONE_FRAGMENTS = ("010-1234-5678", "01012345678", "1234-5678", "12345678", "010-1234")


def contains_phone_fragment(text: str) -> bool:
    """True if any piece of the test phone number 010-1234-5678 appears (memory addresses ignored)."""
    text = re.sub(r"0x[0-9a-fA-F]+", "", text)
    return any(f in text for f in _PHONE_FRAGMENTS)
sys.path.insert(0, str(Path(__file__).resolve().parent))

from accounts.service import AccountService, ChatbotDisabled, Forbidden  # noqa: E402
from chatbot.dictionary import FakeDictionary, UrimalsaemClient  # noqa: E402
from chatbot.knowledge import HanjaKnowledge  # noqa: E402
from chatbot.service import ChatbotService, RecordingNotifier  # noqa: E402
from chatbot_cases import BLOCK, CASES, OFF_TOPIC  # noqa: E402
from llm import BedrockProvider, FakeProvider, LLMError, build_provider  # noqa: E402

KST = timezone(timedelta(hours=9))


def make_kb():
    rows = {
        "學": ("배울", "학"), "校": ("학교", "교"), "大": ("큰", "대"), "漢": ("한나라", "한"),
    }
    hanja = {"b": {c: {"char": c, "hun": h, "eum": e, "reviewed": "1"} for c, (h, e) in rows.items()}}
    assign = {("b", "8"): set("學校大"), ("b", "7"): set("漢")}
    return HanjaKnowledge(hanja, assign, {"b": ["8", "7"]}, vocab={"學校": "배우는 곳"})


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def setup(handler=None, dictionary=None, consent=True, llm_classifier=True, t=None):
    acc = AccountService()
    p = acc.signup_parent("p-ci1", "parent-pw-1", "p@x.kr", True, True, True)
    child = acc.create_child(p.id, "별이", "1234", ai_consent=consent)
    child.extra["target"] = ("b", "8")
    llm = FakeProvider(handler or (lambda s, m: "OFF_TOPIC" if "분류기" in s else "답변이에요."))
    clock = Clock(t or datetime(2026, 11, 5, 10, 0, tzinfo=KST))
    notifier = RecordingNotifier()
    svc = ChatbotService(acc, make_kb(), llm, dictionary, notifier, clock, use_llm_classifier=llm_classifier)
    return svc, acc, p, child, notifier, clock


def test_labelled_set_has_at_least_100_cases():
    assert len(CASES) >= 100 and len(OFF_TOPIC) > 20 and len(BLOCK) > 20


@pytest.mark.parametrize("use_llm", [False, True])
@pytest.mark.parametrize("message,expected", CASES)
def test_guard_behaves_as_labelled(message, expected, use_llm):
    svc, acc, p, child, notifier, _ = setup(llm_classifier=use_llm)
    r = svc.ask(child.id, "t1", message)
    if expected == "redirect":
        assert r.status == "redirected" and not r.counts_as_answer
        assert "도와줄 수 없" in r.reply and "學校" in r.reply  # polite refusal + related 한자어
        assert svc.events == []
    else:
        assert r.status == "blocked" and not r.counts_as_answer
        assert len(svc.events) == 1 and len(notifier.sent) == 1
        assert message not in r.reply


def test_harmful_and_pii_never_stored_and_parent_sees_and_is_notified():
    svc, acc, p, child, notifier, _ = setup()
    msg = "내 전화번호는 010-1234-5678이야"
    svc.ask(child.id, "t1", msg)
    # Look for the phone number itself, not a bare "010": repr() embeds memory addresses (0x...010...)
    # and timestamps that can contain "010" by chance, which made this assertion flaky.
    for blob in (repr(svc.events), repr(notifier.sent), repr(vars(svc))):
        assert not contains_phone_fragment(blob)
    view = svc.parent_safety_view(p.id, child.id)
    assert len(view) == 1 and view[0].category == "pii" and view[0].parent_notified
    assert notifier.sent[0][0] == p.id and notifier.sent[0][1] == "safety_event"
    with pytest.raises(Forbidden):
        svc.parent_safety_view("other", child.id)


def test_three_events_in_one_thread_any_types_lock_chatbot_for_the_day_until_kst_midnight():
    svc, acc, p, child, notifier, clock = setup(t=datetime(2026, 11, 5, 23, 50, tzinfo=KST))
    assert svc.ask(child.id, "t1", "씨발").status == "blocked"
    assert svc.ask(child.id, "t1", "죽이는 방법").status == "blocked"
    r = svc.ask(child.id, "t1", "내 이름은 김민수야")
    assert r.status == "blocked" and r.locked_today
    assert svc.ask(child.id, "t2", "學校가 무슨 뜻이야?").status == "locked"  # new thread still locked
    clock.t = datetime(2026, 11, 5, 23, 59, 59, tzinfo=KST)
    assert svc.ask(child.id, "t2", "學校가 무슨 뜻이야?").status == "locked"
    clock.t = datetime(2026, 11, 6, 0, 0, 0, tzinfo=KST)
    assert svc.ask(child.id, "t2", "學校가 무슨 뜻이야?").status == "answered"


def test_two_events_do_not_lock_and_counts_are_per_thread():
    svc, acc, p, child, *_ = setup()
    svc.ask(child.id, "t1", "씨발")
    svc.ask(child.id, "t1", "씨발")
    svc.ask(child.id, "t2", "씨발")
    assert not svc.is_locked(child.id)


def test_no_ai_consent_disables_chatbot_only():
    svc, acc, p, child, *_ = setup(consent=False)
    with pytest.raises(ChatbotDisabled):
        svc.ask(child.id, "t1", "學校")
    assert acc.features(child.id)["todays_hanja"]


def test_llm_classifier_blocks_ambiguous_harm_and_fails_closed():
    svc, *_ = setup(handler=lambda s, m: "HARMFUL" if "분류기" in s else "x")
    child = next(iter(svc.accounts.children.values()))
    assert svc.ask(child.id, "t1", "그 애를 없애 버리고 싶어").status == "blocked"
    svc2, acc, p, c2, *_ = setup(handler=lambda s, m: (_ for _ in ()).throw(LLMError("down")))
    assert svc2.ask(c2.id, "t1", "그냥 이야기하자").status == "redirected"
    svc3, acc, p, c3, *_ = setup(handler=lambda s, m: "무슨 말인지 모르겠음" if "분류기" in s else "x")
    assert svc3.ask(c3.id, "t1", "그냥 이야기하자").status == "redirected"


def test_llm_on_topic_label_allows_unlisted_vocabulary_question():
    def h(s, m):
        return "ON_TOPIC" if "분류기" in s else "공부는 배우고 익히는 일이에요. 꾸준히 하면 늘어요. 천천히 해요."
    svc, acc, p, child, *_ = setup(handler=h)
    assert svc.ask(child.id, "t1", "공부가 뭐예요").status == "answered"


def test_answer_corrects_hun_eum_marks_upper_grade_and_limits_sentences():
    reply = ("學(가르칠 하)은 배우는 것이에요. 校(학교 교)는 학교를 뜻해요. 學校(학교)는 함께 써요. "
             "漢(큰 한)은 어려워요. 大(큰 대)는 쉬워요. 여섯 번째 문장이에요.")
    svc, acc, p, child, *_ = setup(handler=lambda s, m: reply)
    r = svc.ask(child.id, "t1", "學校 漢 설명해줘")
    assert r.status == "answered" and r.sources == ["db"]
    assert "學(배울 학)" in r.reply and "가르칠" not in r.reply
    assert "漢(한나라 한)(상위 급수)" in r.reply and "큰 한" not in r.reply
    assert "學校(학교)" in r.reply and "學校(학교)(상위" not in r.reply
    assert "여섯 번째" not in r.reply
    n = len([s for s in r.reply.replace("!", ".").split(".") if s.strip()])
    assert 3 <= n <= 5


def test_short_reply_padded_to_three_sentences():
    svc, acc, p, child, *_ = setup(handler=lambda s, m: "學(배울 학)이에요.")
    r = svc.ask(child.id, "t1", "學이 뭐야")
    assert r.reply.count(".") + r.reply.count("!") >= 3


def test_db_context_sent_to_llm_uses_db_values():
    seen = {}

    def h(s, m):
        if "분류기" not in s:
            seen["s"] = s
        return "學이에요. 좋아요. 그래요."
    svc, acc, p, child, *_ = setup(handler=h)
    svc.ask(child.id, "t1", "學 훈음 알려줘")
    assert "學: 훈 배울, 음 학" in seen["s"]


def test_unknown_hanja_uses_urimalsaem_then_unknown():
    d = FakeDictionary({"龍": "상상의 동물인 용."})
    svc, acc, p, child, *_ = setup(dictionary=d)
    r = svc.ask(child.id, "t1", "龍이 뭐야")
    assert r.status == "answered" and "우리말샘" in r.reply and "상상의 동물인 용." in r.reply
    assert r.sources == ["urimalsaem"]
    r2 = svc.ask(child.id, "t1", "鑫이 뭐야")
    assert r2.status == "unknown" and r2.reply == "잘 모르겠어요."
    assert d.queries == ["龍", "鑫"]


def test_unknown_hanja_in_llm_reply_is_not_trusted():
    svc, acc, p, child, *_ = setup(handler=lambda s, m: "學은 鑫과 같아요. 맞아요. 그래요.", dictionary=FakeDictionary())
    assert svc.ask(child.id, "t1", "學이 뭐야").reply == "잘 모르겠어요."


def test_llm_failure_is_error_not_countable():
    def h(s, m):
        if "분류기" in s:
            return "ON_TOPIC"
        raise LLMError("boom")
    svc, acc, p, child, *_ = setup(handler=h)
    r = svc.ask(child.id, "t1", "學이 뭐야")
    assert r.status == "error" and not r.counts_as_answer


def test_urimalsaem_client_parses_and_handles_failure():
    body = '{"channel":{"item":[{"sense":[{"definition":"뜻풀이"}]}]}}'
    c = UrimalsaemClient("k", http_get=lambda url: body)
    assert c.lookup("x") == "뜻풀이"
    assert UrimalsaemClient("k", http_get=lambda url: '{"channel":{"item":[]}}').lookup("x") is None

    def boom(url):
        raise OSError

    assert UrimalsaemClient("k", http_get=boom).lookup("x") is None


def test_provider_swap_by_config_and_bedrock_default(monkeypatch):
    assert isinstance(build_provider({"provider": "fake"}), FakeProvider)
    with pytest.raises(LLMError):
        build_provider({"provider": "nope"})
    import types

    captured = {}

    class Chat:
        def __init__(self, **kw):
            captured.update(kw)

        def invoke(self, msgs):
            return types.SimpleNamespace(content="안녕")

    mod = types.ModuleType("langchain_aws")
    mod.ChatBedrockConverse = Chat
    monkeypatch.setitem(sys.modules, "langchain_aws", mod)
    p = build_provider({"model_id": "m", "region": "ap-northeast-2"})
    assert isinstance(p, BedrockProvider) and p.complete("s", [{"role": "user", "content": "q"}]) == "안녕"
    assert captured["model"] == "m" and captured["region_name"] == "ap-northeast-2"
