"""Opt-in live tests against the real services (never part of the default run).

Run:  npm --prefix frontend run test:live      (or)
      python -m pytest backend/tests/test_live.py -m "bedrock_live or urimalsaem_live" -p no:cacheprovider -rs

Credentials come from the process env or backend/.env (loaded here only) through the default boto3
chain; nothing is printed. Without keys the tests skip with "AWS credentials not configured".
Calls are kept tiny: Haiku 4.5 for chat + safety classifier, Sonnet 4.6 for question generation,
region ap-northeast-2 (global.* profiles) unless AWS_REGION says otherwise.
"""
import os
import re

import pytest

from app.settings import apply_dotenv, aws_credentials_configured, aws_region
from chatbot.dictionary import UrimalsaemClient
from chatbot.knowledge import HanjaKnowledge
from chatbot.safety import LLMClassifier, gate_message
from chatbot.service import MAX_SENTENCES, MIN_SENTENCES, _SENT_SPLIT
from chatbot_cases import CASES
from hanja_db.store import HanjaStore
from llm import BedrockProvider, build_llm_router
from mockexam.questions import Hanja, generate_ai_questions, verify_question

STORE = HanjaStore.load()


@pytest.fixture(scope="module")
def router():
    apply_dotenv()  # live tests only; process env still wins
    if not aws_credentials_configured():
        pytest.skip("AWS credentials not configured")
    return build_llm_router({"provider": "bedrock", "region": aws_region(os.environ)})


def body_db(body="korea_eomunhoe"):
    return {c: Hanja(c, r.hun, r.eum) for (b, c), r in STORE.readings.items() if b == body and r.complete}


@pytest.mark.bedrock_live
def test_tiers_use_haiku_for_chat_and_sonnet_for_questions(router):
    chat, qgen, cls = (router.for_task(t) for t in ("chat", "question_generation", "safety_classifier"))
    assert isinstance(chat, BedrockProvider) and "haiku-4-5" in chat.model_id
    assert cls is chat
    assert isinstance(qgen, BedrockProvider) and "sonnet-4-6" in qgen.model_id


@pytest.mark.bedrock_live
def test_haiku_chat_answer_is_db_grounded_and_3_to_5_sentences(router):
    from accounts.service import AccountService, FakeIdentityVerifier
    from chatbot.service import ChatbotService

    acc = AccountService(FakeIdentityVerifier())
    p = acc.signup_parent(acc.verifier.complete_verification("live"), "a@b.kr", True, True, True)
    c = acc.create_child(p.id, "별이", "1234", ai_consent=True)
    c.extra["target"] = ("korea_eomunhoe", "8")
    bot = ChatbotService(acc, HanjaKnowledge.from_store(STORE), router.for_task("chat"),
                         classifier_llm=router.for_task("safety_classifier"))
    r = bot.ask(c.id, "live", "學이 무슨 뜻이에요?")
    assert r.status == "answered", r
    n = len([s for s in _SENT_SPLIT.split(r.reply.strip()) if s])
    assert MIN_SENTENCES <= n <= MAX_SENTENCES + 2  # + optional dictionary/notes sentences
    db = STORE.reading("korea_eomunhoe", "學")
    for m in re.finditer(r"學\(([^)]+)\)", r.reply):
        assert m.group(1) == f"{db.hun} {db.eum}"


@pytest.mark.bedrock_live
def test_haiku_streams_first_chunk(router):
    chunks = list(router.for_task("chat").stream("한 문장으로 짧게 답해.", [{"role": "user", "content": "안녕"}]))
    assert chunks and "".join(chunks).strip()


@pytest.mark.bedrock_live
@pytest.mark.parametrize("message,expected", CASES)
def test_labelled_guard_set_with_real_classifier(router, message, expected):
    gate = gate_message(message, LLMClassifier(router.for_task("safety_classifier")))
    assert gate.action == expected, (message, gate)


@pytest.mark.bedrock_live
def test_sonnet_question_generation_is_db_verified(router):
    db = body_db()
    kept, dropped = generate_ai_questions(router.for_task("question_generation"), ["學", "校", "山"], db)
    assert kept, f"no question survived the DB gate; dropped={[(q.char, p) for q, p in dropped]}"
    for q in kept:
        assert verify_question(q, db) == [] and q.source == "ai"


@pytest.fixture(scope="module")
def opendict():
    apply_dotenv()
    if not os.environ.get("OPENDICT_API_KEY"):
        pytest.skip("OPENDICT_API_KEY not configured")
    return UrimalsaemClient(os.environ["OPENDICT_API_KEY"], timeout=10)


@pytest.mark.urimalsaem_live
def test_urimalsaem_real_lookup(opendict):
    for term in ("나무", "학교"):
        d = opendict.lookup(term)
        assert d and isinstance(d, str), f"no definition for {term}"
    assert opendict.lookup("뷁뷁뷁없는말") is None
