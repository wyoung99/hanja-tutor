"""Chatbot + AI question generation pass under every provider selected by config only."""
import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from llm import FakeProvider, LLMProvider, build_provider, register_provider  # noqa: E402
from mockexam import questions as Q  # noqa: E402
import test_chatbot as TC  # noqa: E402

DB = {
    "學": Q.Hanja("學", "배울", "학"), "校": Q.Hanja("校", "학교", "교"),
    "大": Q.Hanja("大", "큰", "대"), "漢": Q.Hanja("漢", "한나라", "한"),
}

GOOD = {"type": "eum", "char": "學", "options": ["학", "교", "대", "한"], "answer": "학",
        "explanation": "'學'의 훈음은 '배울 학'입니다."}
BAD = dict(GOOD, answer="교")  # contradicts DB 음


def respond(system, messages):
    """Single behaviour shared by all providers: classifier / chatbot / question generator."""
    if "분류기" in system:
        return "ON_TOPIC"
    if "출제자" in system:
        return json.dumps([GOOD, BAD], ensure_ascii=False)
    return "學(배울 학)은 배우는 것이에요. 學校(학교)는 배우는 곳이에요. 열심히 공부해요."


class AltProvider(LLMProvider):
    """A second, independently implemented provider (stand-in for e.g. OpenAI)."""

    def __init__(self, **_):
        pass

    def complete(self, system, messages):
        return respond(system, messages)


@pytest.fixture
def providers(monkeypatch):
    register_provider("alt", AltProvider)

    class Chat:  # fake langchain_aws.ChatBedrockConverse
        def __init__(self, **kw):
            pass

        def invoke(self, msgs):
            return types.SimpleNamespace(content=respond(msgs[0][1], msgs[1:]))

    mod = types.ModuleType("langchain_aws")
    mod.ChatBedrockConverse = Chat
    monkeypatch.setitem(sys.modules, "langchain_aws", mod)


CONFIGS = [
    {"provider": "bedrock", "model_id": "m", "region": "ap-northeast-2"},
    {"model_id": "m"},  # default provider == bedrock
    {"provider": "alt"},
]


def make_config(cfg):
    return cfg


@pytest.mark.parametrize("cfg", CONFIGS + ["fake"], ids=["bedrock", "default", "alt", "fake"])
def test_chatbot_and_question_generation_per_provider(providers, cfg):
    llm = FakeProvider(respond) if cfg == "fake" else build_provider(cfg)
    # chatbot
    svc, acc, p, child, *_ = TC.setup()
    svc.llm = llm
    r = svc.ask(child.id, "t1", "學校 설명해줘")
    assert r.status == "answered" and "學(배울 학)" in r.reply
    # question generation: DB-correct kept, DB-contradicting discarded
    kept, dropped = Q.generate_ai_questions(llm, ["學"], DB)
    assert [q.answer for q in kept] == ["학"] and kept[0].source == "ai"
    assert len(dropped) == 1 and "!= DB 음" in dropped[0][1][0]


def test_bedrock_is_default_and_config_is_only_difference(providers):
    from llm import BedrockProvider

    assert isinstance(build_provider({"model_id": "m"}), BedrockProvider)
    assert isinstance(build_provider({"provider": "alt"}), AltProvider)


def test_llm_failure_yields_no_ai_questions():
    from llm import LLMError

    def boom(s, m):
        raise LLMError("down")

    assert Q.generate_ai_questions(FakeProvider(boom), ["學"], DB) == ([], [])
    assert Q.parse_ai_questions("not json") == []


# ---- model tiers: Haiku by default, Sonnet only for reasoning tasks -----------------
from llm import (  # noqa: E402
    BEDROCK_MODEL_IDS, FAST, REASONING, TASK_TIERS, LLMError, LLMRouter, build_llm_router,
)

HAIKU_GLOBAL = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
SONNET_GLOBAL = "global.anthropic.claude-sonnet-4-6"


def test_default_task_tiers_haiku_except_question_generation():
    assert TASK_TIERS == {"chat": FAST, "safety_classifier": FAST, "question_generation": REASONING}


@pytest.mark.parametrize("region,prefix", [("us-east-1", "us."), ("us-west-2", "us."), ("ap-northeast-2", "global.")])
def test_region_table_uses_available_profiles(region, prefix):
    ids = BEDROCK_MODEL_IDS[region]
    assert "haiku-4-5" in ids[FAST] and "sonnet-4-6" in ids[REASONING]
    assert ids[FAST].startswith(prefix) and ids[REASONING].startswith(prefix)


def test_bedrock_region_config_routes_by_task():
    r = build_llm_router({"provider": "bedrock", "region": "ap-northeast-2"})
    assert r.for_task("chat").model_id == HAIKU_GLOBAL
    assert r.for_task("safety_classifier").model_id == HAIKU_GLOBAL
    assert r.for_task("question_generation").model_id == SONNET_GLOBAL
    assert r.for_task("anything_new").model_id == HAIKU_GLOBAL  # unknown task -> default Haiku
    assert all(p.region == "ap-northeast-2" for p in r.providers.values())


def test_default_region_is_seoul():
    r = build_llm_router({"provider": "bedrock"})
    assert r.for_task("chat").region == "ap-northeast-2"


def test_unknown_region_rejected():
    with pytest.raises(LLMError):
        build_llm_router({"provider": "bedrock", "region": "eu-west-1"})


def test_task_tier_override_and_explicit_tiers():
    r = build_llm_router({
        "tiers": {"fast": {"provider": "fake"}, "reasoning": {"provider": "fake"}},
        "task_tiers": {"chat": "reasoning"},
    })
    assert r.tier_of("chat") == REASONING and r.tier_of("safety_classifier") == FAST


def test_legacy_single_model_config_used_for_all_tasks():
    r = build_llm_router({"provider": "bedrock", "model_id": "x", "region": "us-east-1"})
    assert r.for_task("chat") is r.for_task("question_generation")


def test_router_rejects_task_on_missing_tier():
    with pytest.raises(LLMError):
        LLMRouter({FAST: FakeProvider()}, {"question_generation": REASONING})


def test_chatbot_classifier_and_answer_can_use_separate_providers():
    from chatbot.service import ChatbotService
    answer, clf = FakeProvider(), FakeProvider()
    bot = ChatbotService.__new__(ChatbotService)
    ChatbotService.__init__(bot, None, None, answer, classifier_llm=clf)
    assert bot.llm is answer and bot.classifier.provider is clf
