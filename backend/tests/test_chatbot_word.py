"""Short Hangul word questions ("학교", "학교?", "학교 뜻", "학교가 뭐야") are resolved to hanja via the
우리말샘 원어 field (``sense.origin``) and answered with the selected body's DB 훈·음 - not redirected.

Hermetic: the 우리말샘 responses are real API responses captured once (tests/fixtures/urimalsaem_search_*.json),
served to the real UrimalsaemClient parser; the LLM is a FakeProvider; the hanja DB is the real one.
"""
import json
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from accounts.service import AccountService, FakeIdentityVerifier  # noqa: E402
from chatbot.dictionary import DictEntry, FakeDictionary, UrimalsaemClient, parse_entries  # noqa: E402
from chatbot.knowledge import HanjaKnowledge  # noqa: E402
from chatbot.service import BLOCK_REPLY, ChatbotService, RecordingNotifier  # noqa: E402
from chatbot.wordquery import resolve_entries, resolve_word, word_candidates  # noqa: E402
from chatbot_cases import CASES  # noqa: E402
from hanja_db.store import HanjaStore  # noqa: E402
from llm import FakeProvider  # noqa: E402
from usage import CHATBOT, UsageLimitConfig, UsageService  # noqa: E402

KST = timezone(timedelta(hours=9))
FIX = Path(__file__).resolve().parent / "fixtures"
FIXTURES = {"학교": "hakgyo", "형": "hyeong", "사과": "sagwa", "나무": "namu"}
KB = HanjaKnowledge.from_store(HanjaStore.load())
EOMUN, DAEGEOM = "korea_eomunhoe", "daegeom"
ANSWER = "學校는 배우는 곳이에요. 學(배울 학)과 校(학교 교)를 써요. 친구들과 함께 공부해요."
WORD_QUERIES = ["학교", "학교?", "학교 뜻", "학교가 뭐야"]


def fixture_json(term: str) -> str:
    name = FIXTURES.get(term)
    if name is None:
        return '{"channel":{"total":0,"item":[]}}'
    return (FIX / f"urimalsaem_search_{name}.json").read_text(encoding="utf-8")


class FixtureHttp:
    """http_get for UrimalsaemClient: serves the captured responses, counts calls per query term."""

    def __init__(self, delay: float = 0.0):
        self.calls = []
        self.delay = delay

    def __call__(self, url: str) -> str:
        term = parse_qs(urlparse(url).query)["q"][0]
        self.calls.append(term)
        if self.delay:
            time.sleep(self.delay)
        return fixture_json(term)


def fixture_client(**kw) -> UrimalsaemClient:
    return UrimalsaemClient("k", http_get=FixtureHttp(**kw))


class Bot:
    """A chatbot whose fake LLM records the answer prompt; the classifier answers `label`."""

    def __init__(self, label="OFF_TOPIC", body=EOMUN, grade="8", dictionary="fixture", answer=ANSWER,
                 classifier_delay=0.0, limits=None):
        self.label, self.systems = label, []
        ver = FakeIdentityVerifier()
        self.acc = AccountService(ver)
        p = self.acc.signup_parent(ver.complete_verification("ci"), "p@x.kr", True, True, True)
        self.child = self.acc.create_child(p.id, "별이", "1234", ai_consent=True)
        self.child.extra["target"] = (body, grade)
        self.dictionary = fixture_client() if dictionary == "fixture" else dictionary

        def handler(system, msgs):
            if "분류기" in system:
                if classifier_delay:
                    time.sleep(classifier_delay)
                return self.label
            self.systems.append((system, msgs))
            return answer

        self.llm = FakeProvider(handler)
        clock = lambda: datetime(2026, 11, 6, 10, tzinfo=KST)  # noqa: E731
        self.notifier = RecordingNotifier()
        self.usage = UsageService(UsageLimitConfig.from_mapping(limits), clock,
                                  parent_of=lambda cid: (p.id, "별이"), notifier=self.notifier) if limits else None
        self.svc = ChatbotService(self.acc, KB, self.llm, self.dictionary, self.notifier, clock,
                                  use_llm_classifier=label is not None, usage=self.usage)

    def ask(self, msg, mode="ask"):
        if mode == "ask":
            r = self.svc.ask(self.child.id, "t1", msg)
            return {"status": r.status, "reply": r.reply, "category": r.category, "sources": r.sources}, []
        ev = list(self.svc.ask_stream(self.child.id, "t1", msg))
        return ev[-1], ev

    @property
    def answer_prompt(self):
        return self.systems[-1][0] if self.systems else None


# ---- the 우리말샘 원어 field --------------------------------------------------------------------
def test_fixture_is_a_real_response_with_the_origin_field():
    entries = parse_entries(json.loads(fixture_json("학교")))
    assert entries and all(isinstance(e, DictEntry) for e in entries)
    assert {e.origin for e in entries if e.word == "학교" and not e.cat or e.cat == "교육"} == {"學校"}


@pytest.mark.parametrize("term,hanja,definition_start", [
    ("학교", "學校", "일정한 목적"),   # the API lists the slang sense 002 ('교도소') first: basic sense 001 is used
    ("형", "兄", "같은 부모에게서"),
    ("사과", "沙果", "사과나무의 열매"),  # API order (沙果) beats the lower sense number 四科
])
def test_resolve_entries_prefers_hanja_origin_of_general_senses(term, hanja, definition_start):
    res = resolve_entries(term, parse_entries(json.loads(fixture_json(term))))
    assert res.hanja == hanja and res.is_hanja and res.definition.startswith(definition_start)


def test_native_word_is_not_given_hanja():
    res = resolve_entries("나무", parse_entries(json.loads(fixture_json("나무"))))
    assert res is not None and res.hanja is None and not res.is_hanja  # 南無 (불교) / 鑼舞 (무용) ignored


def test_proper_names_and_affixes_and_inexact_words_do_not_resolve():
    entries = [DictEntry("학교 가는 길", "學校가는길", "영화", "영상"), DictEntry("-학", "學", "접사"),
               DictEntry("학교", "鶴橋", "지명", "지명")]
    assert resolve_entries("학교", entries) is None


# ---- which messages are short word questions ------------------------------------------------
@pytest.mark.parametrize("msg,expected", [
    ("학교", ["학교"]), ("학교?", ["학교"]), ("학교 뜻", ["학교"]), ("학교가 뭐야", ["학교가", "학교"]),
    ("학교뜻", ["학교"]), ("학교가뭐야?", ["학교"]), ("학교는 무슨 뜻이야?", ["학교는", "학교"]),
    ("학교 한자로 어떻게 써?", ["학교"]), ("학교에서", ["학교에서", "학교"]), ("사과", ["사과", "사"]),
    ("學校", []), ("학교 가기 싫어", []), ("영어로 사과가 뭐야", []), ("급식 메뉴 알려줘", []),
    ("오늘 날씨 어때?", []), ("ㅋㅋㅋ", []), ("hello", []), ("가나다라마바사아자차카", []), ("", []),
])
def test_word_candidates(msg, expected):
    assert word_candidates(msg) == expected


# ---- gate + prompt: resolved words are answered, not redirected ------------------------------
@pytest.mark.parametrize("mode", ["ask", "stream"])
@pytest.mark.parametrize("msg", WORD_QUERIES)
def test_short_word_is_answered_with_db_hanja_even_if_classifier_says_off_topic(msg, mode):
    b = Bot(label="OFF_TOPIC")
    done, events = b.ask(msg, mode)
    assert done["status"] == "answered", done
    assert "도와줄 수 없" not in done["reply"]
    system = b.answer_prompt
    assert "학생이 묻는 낱말: 학교(學校)" in system
    db = system.split("[DB]\n", 1)[1]
    assert "學: 훈 배울, 음 학" in db and "校: 훈 학교, 음 교" in db
    assert "우리말샘 뜻풀이: 일정한 목적" in system
    assert "3~5문장" in system and "'漢(한나라 한)' 형식" in system  # existing rules kept
    assert b.systems[-1][1][-1] == {"role": "user", "content": msg}  # the child's message is passed through
    assert "學(배울 학)" in done["reply"] and "urimalsaem" in done["sources"]
    if mode == "stream":
        assert [e["type"] for e in events][-1] == "done" and events[0]["type"] == "token"


@pytest.mark.parametrize("body,hun", [(EOMUN, "형"), (DAEGEOM, "맏")])
def test_word_uses_selected_body_reading(body, hun):
    b = Bot(body=body, answer="兄(형 형)은 손윗사람이에요. 형은 나보다 나이가 많아요. 사이좋게 지내요.")
    done, _ = b.ask("형이 뭐야")
    assert done["status"] == "answered"
    assert "학생이 묻는 낱말: 형(兄)" in b.answer_prompt
    assert f"兄: 훈 {hun}, 음 형" in b.answer_prompt
    assert f"兄({hun} 형)" in done["reply"]  # post-processing corrects to the selected body


def test_upper_grade_chars_are_marked_in_the_prompt():
    b = Bot(body=EOMUN, grade="8")  # 形 (같은 소리 다른 한자어) is 6II for 한국어문회
    b.ask("형")
    assert "形: 훈 모양, 음 형 (상위 급수)" in b.answer_prompt
    assert "같은 소리의 다른 한자어: 形" in b.answer_prompt  # 邢 (not in the DB) is left out


def test_resolved_word_counts_usage_like_any_answer_and_redirect_does_not():
    b = Bot(label="OFF_TOPIC", limits={CHATBOT: 5})
    assert b.ask("학교", "stream")[0]["status"] == "answered"
    assert b.usage.snapshot(b.child.id, CHATBOT)["used"] == 1
    assert b.ask("학교 뜻")[0]["status"] == "answered"
    assert b.usage.snapshot(b.child.id, CHATBOT)["used"] == 2
    assert b.ask("심심해", "stream")[0]["status"] == "redirected"  # not a dictionary word: classifier decides
    assert b.usage.snapshot(b.child.id, CHATBOT)["used"] == 2


# ---- no fabrication ------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["ask", "stream"])
def test_native_word_is_not_forced_on_topic_and_never_gets_invented_hanja(mode):
    assert Bot(label="OFF_TOPIC").ask("나무", mode)[0]["status"] == "redirected"
    b = Bot(label="ON_TOPIC", answer="나무는 줄기가 단단한 식물이에요. 한자가 없는 우리말이에요. 숲에 많아요.")
    done, _ = b.ask("나무 뜻", mode)
    assert done["status"] == "answered"
    assert "한자 원어가 없는 우리말" in b.answer_prompt and "한자를 지어 붙이지 말고" in b.answer_prompt
    assert b.answer_prompt.endswith("[DB]\n")  # nothing to ground: no hanja in the prompt


@pytest.mark.parametrize("mode", ["ask", "stream"])
def test_unknown_word_without_origin_is_not_fabricated(mode):
    b = Bot(label="ON_TOPIC", answer="그 낱말은 잘 모르겠어요. 다른 낱말을 물어봐요. 같이 찾아봐요.")
    done, _ = b.ask("뷁뷁", mode)  # 우리말샘: no entry; no DB hanja reads 뷁 -> no syllable list either
    assert done["status"] == "answered"
    assert "학생이 묻는 낱말" not in b.answer_prompt and b.answer_prompt.endswith("[DB]\n")


@pytest.mark.parametrize("mode", ["ask", "stream"])
def test_without_dictionary_hit_syllable_candidates_are_listed_not_a_word(mode):
    b = Bot(label="ON_TOPIC", dictionary=None)  # no API key
    done, _ = b.ask("학교 뜻", mode)
    assert done["status"] == "answered" and done["sources"] == ["db"]
    reply = done["reply"]
    assert "사전에서 확인하지 못했어요" in reply and "이런 한자가 있어요" in reply
    assert "學(배울 학)" in reply and "校(학교 교)" in reply and "敎(가르칠 교)" in reply
    assert "學校" not in reply  # never composes a word
    assert b.systems == []  # deterministic: no LLM answer call
    # a char above the target grade is marked
    assert "(상위 급수)" in Bot(label="ON_TOPIC", dictionary=None).ask("형", mode)[0]["reply"]


def test_without_dictionary_the_classifier_still_decides():
    assert Bot(label="OFF_TOPIC", dictionary=None).ask("학교")[0]["status"] == "redirected"


# ---- safety still wins -------------------------------------------------------------------------
class EverythingIsHanja(FakeDictionary):
    def search(self, term):
        self.searches.append(term)
        return [DictEntry(term, "學" * len(term), "뜻")]


@pytest.mark.parametrize("mode", ["ask", "stream"])
@pytest.mark.parametrize("msg", ["씨발", "병신", "ㅅㅂ", "꺼져", "개새끼"])
def test_short_profanity_is_still_blocked_by_rules_before_any_lookup(msg, mode):
    d = EverythingIsHanja()
    b = Bot(label="ON_TOPIC", dictionary=d)
    done, events = b.ask(msg, mode)
    assert done["status"] == "blocked" and done["reply"] == BLOCK_REPLY
    assert d.searches == [] and b.systems == []
    assert [e["type"] for e in events] in ([], ["done"])


@pytest.mark.parametrize("mode", ["ask", "stream"])
@pytest.mark.parametrize("label,category", [("PII", "pii"), ("HARMFUL", "violence")])
def test_classifier_pii_and_harmful_still_block_a_resolved_word(label, category, mode):
    b = Bot(label=label, dictionary=EverythingIsHanja(), classifier_delay=0.2 if mode == "stream" else 0.0)
    done, events = b.ask("민준", mode)  # a name-like word that the dictionary "resolves"
    assert done["status"] == "blocked" and done["category"] == category
    assert [e["type"] for e in events] in ([], ["done"])  # no answer text before the classifier allowed
    assert len(b.svc.events) == 1 and len(b.notifier.sent) == 1
    assert "민준" not in repr(b.svc.events)


@pytest.mark.parametrize("message,expected", CASES)
def test_labelled_set_unchanged_with_a_dictionary(message, expected):
    """The 114 labelled cases stay 100% as labelled with 우리말샘 configured (OFF_TOPIC classifier)."""
    b = Bot(label="OFF_TOPIC")
    done, _ = b.ask(message)
    assert done["status"] == ("redirected" if expected == "redirect" else "blocked")


# ---- latency: cache, concurrency, budget -----------------------------------------------------
def test_lookups_are_cached_in_process_and_failures_are_not():
    http = FixtureHttp()
    c = UrimalsaemClient("k", http_get=http)
    for _ in range(3):
        assert resolve_word(c, "학교")[1].hanja == "學校"
    assert c.lookup("학교").startswith("죄수들의")  # lookup() keeps its old first-definition behaviour
    assert http.calls == ["학교"]

    calls = []

    def flaky(url):
        calls.append(url)
        if len(calls) == 1:
            raise OSError("down")
        return fixture_json("학교")

    c2 = UrimalsaemClient("k", http_get=flaky)
    assert c2.search("학교") is None and c2.search("학교") and len(calls) == 2


def test_lru_is_bounded():
    c = UrimalsaemClient("k", http_get=FixtureHttp(), cache_size=2)
    for t in ("학교", "형", "사과"):
        c.search(t)
    assert c._cache.get("학교") is None and c._cache.get("사과") is not None


def test_slow_dictionary_is_abandoned_after_the_budget():
    c = fixture_client(delay=1.0)
    t0 = time.monotonic()
    cands, res, looked = resolve_word(c, "학교", budget=0.1)
    assert time.monotonic() - t0 < 0.5 and res is None and not looked


def test_lookup_overlaps_the_classifier_in_the_stream():
    """Dictionary 0.3s and classifier 0.3s run concurrently: the first token comes well before 0.6s."""
    b = Bot(label="OFF_TOPIC", classifier_delay=0.3, dictionary=fixture_client(delay=0.3))
    seen = threading.Event()
    t0 = time.monotonic()
    for ev in b.svc.ask_stream(b.child.id, "t1", "학교"):
        if ev["type"] == "token":
            first = time.monotonic() - t0
            seen.set()
            break
    assert seen.is_set() and first < 0.55, first


@pytest.mark.parametrize("mode", ["ask", "stream"])
def test_word_char_missing_from_db_gets_one_urimalsaem_note(mode):
    """사과 -> 沙果: 沙 is not in the 한국어문회 DB -> the existing 우리말샘 note path, once (not per mention)."""
    d = FakeDictionary({"沙": "모래."}, words={"사과": parse_entries(json.loads(fixture_json("사과")))})
    b = Bot(dictionary=d, answer="사과는 沙果라고 써요. 沙(모래 사)와 果(실과 과)예요. 沙는 모래라는 뜻이에요.")
    done, _ = b.ask("사과", mode)
    assert done["status"] == "answered"
    assert "학생이 묻는 낱말: 사과(沙果)" in b.answer_prompt
    assert "果: 훈 실과, 음 과 (상위 급수)" in b.answer_prompt and "沙:" not in b.answer_prompt.split("[DB]")[1]
    assert done["reply"].count("'沙'은(는)") == 1 and "우리말샘에서 찾았어요: 모래." in done["reply"]
