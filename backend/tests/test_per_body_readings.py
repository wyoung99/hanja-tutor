"""훈·음 are per exam body (decision 2026-11-05): shown, asked, graded and corrected with the child's
selected body's value, never mixed across bodies. Uses the real DB (data/hanja_db/hanja_by_body.csv)."""
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from accounts.service import AccountService, FakeIdentityVerifier  # noqa: E402
from app.main import hanja_by_body  # noqa: E402
from chatbot.knowledge import HanjaKnowledge  # noqa: E402
from chatbot.service import ChatbotService, postprocess  # noqa: E402
from hanja_db.store import HanjaStore  # noqa: E402
from llm import FakeProvider  # noqa: E402
from mockexam import formats as F  # noqa: E402
from mockexam import questions as Q  # noqa: E402
from mockexam.service import MockExamService, cumulative_pool  # noqa: E402

KST = timezone(timedelta(hours=9))
STORE = HanjaStore.load()
HANJA = hanja_by_body(STORE)
ASSIGN = STORE.assignment_sets()
EOMUN, DAEGEOM = "korea_eomunhoe", "daegeom"


def test_store_keeps_both_bodies_values_for_the_same_char():
    assert STORE.reading(EOMUN, "兄").hun_eum == "형 형"
    assert STORE.reading(DAEGEOM, "兄").hun_eum == "맏 형"
    assert (STORE.reading(EOMUN, "易").eum, STORE.reading(DAEGEOM, "易").eum) == ("역", "이")
    # NFC: the official file stores 金 as U+F90A; lookups by either code point agree
    assert STORE.reading(EOMUN, "金") == STORE.reading(EOMUN, "金") is not None
    assert STORE.reading(EOMUN, "金").char_raw == "金"
    # 1:1 with assignments, no cross-body leakage
    for (body, grade), chars in STORE.assignments.items():
        assert all(STORE.reading(body, c).grade == grade for c in chars)


def _exam_with(body, grade, char, qtype):
    pool = lambda b, g: cumulative_pool(b, g, STORE.bodies[b], ASSIGN, HANJA[b])  # noqa: E731
    for seed in range(200):
        svc = MockExamService(F.load_formats(), pool, {}, pending=F.load_pending())
        ex = svc.start("p", body, grade, seed=seed)
        qs = [q for q in ex.questions if q.char == char and q.type == qtype]
        if qs:
            return svc, ex, qs[0]
    raise AssertionError(f"no {qtype} question about {char} in 200 seeds")


@pytest.mark.parametrize("body,expected,other", [(EOMUN, "형 형", "맏 형"), (DAEGEOM, "맏 형", "형 형")])
def test_mock_exam_asks_and_grades_with_selected_body_reading(body, expected, other):
    svc, ex, q = _exam_with(body, "8", "兄", "hun_eum")
    assert q.answer == expected and expected in q.options and other not in q.options
    assert expected in q.explanation and Q.verify_question(q, HANJA[body]) == []
    svc.answer(ex.exam_id, q.id, other)  # the other body's value is wrong here
    r = svc.submit(ex.exam_id)
    w = next(x for x in r["wrong"] if x["id"] == q.id)
    assert w["answer"] == expected and w["your_answer"] == other


@pytest.mark.parametrize("body,expected", [(EOMUN, "형 형"), (DAEGEOM, "맏 형")])
def test_hun_to_hanja_prompt_uses_selected_body_reading(body, expected):
    # 8급 formats have no hun_to_hanja type, so build one from the body's own pool
    q = Q.build_question("x", "hun_to_hanja", 1, HANJA[body]["兄"], list(HANJA[body].values()), random.Random(1))
    assert f"'{expected}'" in q.prompt and q.answer == "兄"
    assert Q.verify_question(q, HANJA[body]) == []


def test_db_crosscheck_rejects_other_bodys_reading():
    h = HANJA[DAEGEOM]["兄"]
    wrong = Q.Question("ai1", "hun_eum", 1, "다음 한자의 훈(뜻)과 음(소리)은? 兄", "兄",
                       ("형 형", "아비 부", "어미 모", "아우 제"), "형 형", "'兄'의 훈음은 '형 형'입니다.", "ai")
    assert Q.verify_question(wrong, HANJA[DAEGEOM])  # 어문회 value is an error for 대한검정회
    assert Q.verify_question(wrong, HANJA[EOMUN]) == []
    assert h.hun_eum == "맏 형"


def test_incomplete_reading_is_never_asked():
    # 대한검정회 준3 取 has empty 훈·음 in the source (pending human review): kept out of exam pools
    assert STORE.reading(DAEGEOM, "取") is not None and not STORE.reading(DAEGEOM, "取").complete
    assert "取" not in HANJA[DAEGEOM]
    assert "取" not in {h.char for h in cumulative_pool(DAEGEOM, "3", STORE.bodies[DAEGEOM], ASSIGN, HANJA[DAEGEOM])}


# ---- chatbot -------------------------------------------------------------------------------
KB = HanjaKnowledge.from_store(STORE)


def test_knowledge_entry_is_per_body_and_never_borrowed():
    assert KB.entry("兄", EOMUN)["hun"] == "형" and KB.entry("兄", DAEGEOM)["hun"] == "맏"
    assert KB.entry("兄", None) is None
    only_eomunhoe = next(c for c in STORE.cumulative_chars(EOMUN, "4") if STORE.reading(DAEGEOM, c) is None)
    assert KB.entry(only_eomunhoe, EOMUN) and KB.entry(only_eomunhoe, DAEGEOM) is None


@pytest.mark.parametrize("body,expected", [(EOMUN, "兄(형 형)"), (DAEGEOM, "兄(맏 형)")])
def test_chatbot_corrects_hun_eum_to_selected_body(body, expected):
    for llm_says in ("兄(형 형)은 손윗사람이에요.", "兄(맏 형)은 손윗사람이에요."):
        text, bad, _ = postprocess(llm_says, KB, (body, "8"), None)
        assert bad == [] and text.startswith(expected)


def _bot(body):
    ver = FakeIdentityVerifier()
    acc = AccountService(ver)
    p = acc.signup_parent(ver.complete_verification("ci"), "p@x.kr", True, True, True)
    ch = acc.create_child(p.id, "별이", "1234", ai_consent=True)
    ch.extra["target"] = (body, "8")
    seen = {}

    def handler(system, msgs):
        seen["system"] = system
        return "兄(형 형)은 언니나 오빠를 말해요."

    bot = ChatbotService(acc, KB, FakeProvider(handler), None, None,
                         lambda: datetime(2026, 11, 5, 10, tzinfo=KST), use_llm_classifier=False)
    return bot, ch, seen


@pytest.mark.parametrize("body,hun", [(EOMUN, "형"), (DAEGEOM, "맏")])
def test_chatbot_grounds_prompt_and_answer_in_selected_body(body, hun):
    bot, ch, seen = _bot(body)
    r = bot.ask(ch.id, "t1", "兄은 무슨 뜻이에요?")
    assert r.status == "answered"
    assert f"兄: 훈 {hun}, 음 형" in seen["system"]
    assert f"兄({hun} 형)" in r.reply


# ---- reports -------------------------------------------------------------------------------
@pytest.mark.parametrize("body,hun", [(EOMUN, "형"), (DAEGEOM, "맏")])
def test_report_weak_hanja_carries_selected_body_reading(body, hun):
    from reports.service import ReportService

    def reading_for(child_id, ch):
        r = STORE.reading(body, ch)
        return {"hun": r.hun, "eum": r.eum} if r else None

    rep = ReportService(None, None, None, None, None, reading_for=reading_for)
    assert rep._weak("c1", "兄", 2) == {"char": "兄", "misses": 2, "hun": hun, "eum": "형"}
