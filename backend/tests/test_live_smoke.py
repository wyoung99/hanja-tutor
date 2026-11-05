"""Stage 1 live smoke: a SMALL set of real calls so the evaluator can verify Bedrock / 우리말샘 / AC9 p95.

Selected only with ``-m live_smoke`` (``npm --prefix frontend run test:all`` runs it after the hermetic unit
tests). It loads backend/.env itself (or the file named by HANJA_DOTENV); unit tests stay hermetic.
Missing AWS credentials or OPENDICT_API_KEY make these tests FAIL -- never skip -- because the point of the
set is to prove the real calls work.

Calls per run (approx.): Haiku 4.5 1 chat + 1 classifier; Sonnet 4.6 1 question generation; 우리말샘 1 lookup;
p95 run on a local uvicorn: 1 warm-up + 30 measured questions, each = 1 Haiku classifier call + 1 Haiku
streamed answer -> ~22 Haiku calls. Total ~25 Bedrock calls + 1 dictionary call. The 114-case labelled
safety set is NOT part of this (it stays in ``bedrock_live``).
"""
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.settings import DOTENV_PATH, apply_dotenv, aws_credentials_configured, aws_region
from chatbot.dictionary import UrimalsaemClient
from chatbot.knowledge import HanjaKnowledge
from chatbot.safety import LLMClassifier
from chatbot.service import MAX_SENTENCES, MIN_SENTENCES, _SENT_SPLIT
from hanja_db.store import HanjaStore
from llm import BedrockProvider, build_llm_router
from mockexam.questions import Hanja, generate_ai_questions, verify_question

pytestmark = pytest.mark.live_smoke
ROOT = Path(__file__).resolve().parents[2]
STORE = HanjaStore.load()
P95_N = 30  # 10 made p95 ~= the single slowest call, so one slow Bedrock reply failed Stage 1
P95_OUT = ROOT / "reports" / "p95-eval.json"


def _dotenv() -> Path:
    return Path(os.environ.get("HANJA_DOTENV") or DOTENV_PATH)


@pytest.fixture(scope="module")
def live_env():
    path = _dotenv()
    apply_dotenv(path)  # process env still wins
    if not aws_credentials_configured():
        pytest.fail(f"live_smoke needs real AWS credentials: AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY not set "
                    f"(looked in the process env and {path}). Stage 1 must verify real Bedrock calls, so this "
                    f"fails instead of skipping.", pytrace=False)
    return os.environ


@pytest.fixture(scope="module")
def router(live_env):
    return build_llm_router({"provider": "bedrock", "region": aws_region(live_env)})


def test_haiku_chat_answer_uses_db_reading_and_3_to_5_sentences(router):
    from accounts.service import AccountService, FakeIdentityVerifier
    from chatbot.service import ChatbotService

    chat = router.for_task("chat")
    assert isinstance(chat, BedrockProvider) and "haiku-4-5" in chat.model_id
    acc = AccountService(FakeIdentityVerifier())
    p = acc.signup_parent(acc.verifier.complete_verification("smoke"), "a@b.kr", True, True, True)
    c = acc.create_child(p.id, "별이", "1234", ai_consent=True)
    c.extra["target"] = ("korea_eomunhoe", "8")
    bot = ChatbotService(acc, HanjaKnowledge.from_store(STORE), chat, use_llm_classifier=False)  # 1 Haiku call
    r = bot.ask(c.id, "smoke", "學의 훈과 음을 알려 주세요.")
    assert r.status == "answered", r
    n = len([s for s in _SENT_SPLIT.split(r.reply.strip()) if s])
    assert MIN_SENTENCES <= n <= MAX_SENTENCES, (n, r.reply)
    db = STORE.reading("korea_eomunhoe", "學")
    assert db.hun.strip() in r.reply and db.eum.strip() in r.reply, (db, r.reply)  # the selected body's DB value
    for annotated in re.findall(r"學\(([^)]+)\)", r.reply):
        assert annotated == f"{db.hun.strip()} {db.eum.strip()}"


def test_haiku_safety_classifier_labels_off_topic(router):
    cls = router.for_task("safety_classifier")
    assert isinstance(cls, BedrockProvider) and "haiku-4-5" in cls.model_id
    assert LLMClassifier(cls).classify("어제 프로야구 경기 누가 이겼어?") == "OFF_TOPIC"  # 1 Haiku call


def test_sonnet_question_generation_passes_the_db_check(router):
    qgen = router.for_task("question_generation")
    assert isinstance(qgen, BedrockProvider) and "sonnet-4-6" in qgen.model_id
    db = {c: Hanja(c, r.hun, r.eum) for (b, c), r in STORE.readings.items() if b == "korea_eomunhoe" and r.complete}
    kept, dropped = generate_ai_questions(qgen, ["學", "校"], db)  # 1 Sonnet call
    assert kept, f"no question survived the DB gate; dropped={[(q.char, p) for q, p in dropped]}"
    for q in kept:
        assert verify_question(q, db) == [] and q.source == "ai"


def test_urimalsaem_lookup(live_env):
    key = live_env.get("OPENDICT_API_KEY")
    if not key:
        pytest.fail(f"OPENDICT_API_KEY not set (process env / {_dotenv()}); live_smoke needs a real 우리말샘 call",
                    pytrace=False)
    d = UrimalsaemClient(key, timeout=10).lookup("학교")  # 1 dictionary call
    assert d and isinstance(d, str)


def test_p95_first_token_under_3s_on_local_uvicorn(live_env):
    """AC9: 10 questions to POST /api/children/{id}/chat/stream on a local uvicorn with real Bedrock."""
    sys.path.insert(0, str(ROOT / "backend" / "scripts"))
    import httpx
    import measure_p95 as m

    port = m.free_port()
    proc = m.start_server(port)  # loads backend/.env itself (real Bedrock); fake LLM would be reported below
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30) as c:
            health = c.get("/api/health").json()
            cid = m.setup_child(c)
            m.ask(c, cid, "一은 무슨 뜻이에요?")  # warm-up, not counted
            samples = [m.ask(c, cid, m.QUESTIONS[i % len(m.QUESTIONS)]) for i in range(P95_N)]
    finally:
        proc.terminate()
        proc.wait(timeout=30)
    ft = [s["first_token_s"] for s in samples if s["first_token_s"] is not None]
    real = (health.get("llm") or {}).get("chat") == "BedrockProvider"
    p95 = m.p95(ft)
    out = {"measured_at": datetime.now(timezone.utc).isoformat(), "environment": "local uvicorn (live_smoke)",
           "llm": health.get("llm"), "real_bedrock": real, "sample_size": len(samples), "with_first_token": len(ft),
           "first_token_p50_s": sorted(ft)[len(ft) // 2] if ft else None, "first_token_p95_s": p95,
           "first_token_max_s": max(ft) if ft else None, "slo_s": m.SLO_SECONDS,
           "errors": sum(1 for s in samples if s["status"] not in ("answered", "unknown")),
           "pass": bool(real and ft and len(ft) == len(samples) and p95 < m.SLO_SECONDS), "samples": samples}
    P95_OUT.parent.mkdir(parents=True, exist_ok=True)
    P95_OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    assert real, f"server did not use Bedrock (LLM_CONFIG missing in {_dotenv()}?): {health.get('llm')}"
    assert len(ft) == len(samples) and out["errors"] == 0, samples
    assert p95 < m.SLO_SECONDS, f"first-token p95 {p95}s >= {m.SLO_SECONDS}s (reports/p95-eval.json)"
