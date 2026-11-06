"""Input guard: harmful / personal-info detection and topic restriction.

Rules (regex + dictionary) run first; an LLM classifier is consulted only for
messages the rules neither block nor recognise as on-topic. LLM failure or an
unparseable label fails closed (treated as off-topic -> polite redirect).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from llm import LLMError, LLMProvider

HANJA_RE = re.compile("[㐀-䶿一-鿿豈-﫿]")

# ---- personal information patterns ------------------------------------------
PII_PATTERNS = {
    "phone": re.compile(r"(?<!\d)0\d{1,2}[-.\s]?\d{3,4}[-.\s]?\d{4}(?!\d)"),
    "rrn": re.compile(r"(?<!\d)\d{6}[-\s]?[1-4]\d{6}(?!\d)"),
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
    "address": re.compile(
        r"([가-힣]{1,6}(특별시|광역시|특별자치시|특별자치도|도)\s*[가-힣]{1,8}(시|군|구))"
        r"|((서울|부산|대구|인천|광주|대전|울산|세종)(시)?\s*[가-힣]{1,8}(구|군))|([가-힣]{2,6}시\s*[가-힣]{1,6}(구|군))"
        r"|([가-힣]{1,8}(구|군)\s*[가-힣0-9]{1,10}(동|읍|면|로|길)\s*\d*)"
        r"|([가-힣0-9]+(로|길)\s*\d+(-\d+)?\s*(번길|번지)?)"
        r"|(\d+\s*동\s*\d+\s*호)|(\d+번지)|(우리\s*집\s*(주소|은\s*[가-힣]+\s*(동|구|시)))"
    ),
    "name": re.compile(r"(내|제|저의|나의)\s*(진짜\s*)?(본명|이름)\s*(은|는|이)\s*[가-힣]{2,4}"),
    "school": re.compile(r"[가-힣]{2,8}초등학교\s*\d\s*학년|[가-힣]{2,8}초등학교\s*(에\s*)?(다녀|다니|학생)"),
    "contact": re.compile(r"(카톡|카카오톡|인스타|전화번호|연락처|핸드폰\s*번호)\s*(아이디|id|번호)?\s*(는|은|:)?\s*[\w.-]{4,}", re.I),
}

# ---- harmful content ---------------------------------------------------------
_PROFANITY = [
    r"씨발", r"시발(?!점|역|지|하|자)", r"ㅅㅂ", r"ㅆㅂ", r"병신", r"ㅂㅅ", r"개새끼", r"새끼야", r"좆", r"지랄", r"미친놈", r"미친년",
    r"존나", r"졸라", r"닥쳐", r"썅", r"꺼져", r"느금마", r"엿먹", r"씹새", r"개년", r"걸레같", r"또라이", r"븅신", r"염병", r"ㅈㄹ",
    r"fuck", r"shit", r"bitch",
]
_VIOLENCE = [
    r"죽여", r"죽이", r"죽어버려", r"죽고\s*싶", r"자살", r"자해", r"목\s*매", r"때려", r"때리는\s*방법", r"폭탄\s*(만들|만드|제조)", r"칼로\s*(찌|베)",
    r"총\s*(쏘|으로)", r"괴롭히는\s*방법", r"왕따\s*시키", r"독\s*(을|를)\s*(타|먹)", r"불\s*질러", r"살인", r"학살", r"고문",
    r"야한", r"섹스", r"포르노", r"성관계", r"마약", r"담배\s*(사|피우는)", r"술\s*(마시는\s*방법|사는\s*방법)",
]
_PROFANITY_RE = re.compile("|".join(_PROFANITY), re.I)
_VIOLENCE_RE = re.compile("|".join(_VIOLENCE), re.I)
_STRIP_RE = re.compile(r"[\s\W_]+", re.UNICODE)

# ---- topic -------------------------------------------------------------------
_TOPIC_RE = re.compile(
    "|".join(
        [
            r"한자", r"한문", r"훈음", r"훈과\s*음", r"뜻과\s*음", r"부수", r"획순", r"획수", r"급수", r"한자어", r"사자성어", r"고사성어",
            r"어휘", r"낱말", r"단어", r"무슨\s*뜻", r"뜻이", r"뜻은", r"뜻\s*(좀\s*)?(알려|가르쳐|설명)", r"무슨\s*말", r"교과\s*(서)?\s*(어휘|용어)",
            r"(수학|과학|사회|국어)\s*용어", r"용어", r"속담", r"관용어", r"유의어", r"반대말", r"비슷한\s*말", r"한글\s*독음", r"독음",
            r"우리말", r"순우리말", r"의미가\s*뭐", r"어원", r"훈이", r"음이\s*뭐", r"한자로",
        ]
    )
)


@dataclass(frozen=True)
class Verdict:
    category: Optional[str]  # None, or "pii" / "profanity" / "violence"
    source: str = "rule"     # "rule" | "llm"

    @property
    def blocked(self) -> bool:
        return self.category is not None


def detect_pii(text: str) -> Optional[str]:
    for name, pat in PII_PATTERNS.items():
        if pat.search(text):
            return name
    return None


def detect_rule_harm(text: str) -> Optional[str]:
    """Rule-based category for harmful/personal input, else None."""
    squashed = _STRIP_RE.sub("", text.lower())  # defeats '시 발', 'ㅅ.ㅂ' spacing tricks
    if _PROFANITY_RE.search(text) or _PROFANITY_RE.search(squashed):
        return "profanity"
    if _VIOLENCE_RE.search(text) or _VIOLENCE_RE.search(squashed):
        return "violence"
    if detect_pii(text):
        return "pii"
    return None


def rule_on_topic(text: str) -> bool:
    return bool(HANJA_RE.search(text) or _TOPIC_RE.search(text))


_CLASSIFIER_SYSTEM = (
    "너는 초등학생용 한자·어휘 학습 챗봇의 입력 분류기다. 학생 메시지를 아래 라벨 중 하나로만 답하라.\n"
    "HARMFUL: 욕설·폭력·성적·자해 등 유해 내용\nPII: 전화번호·주소·실명 등 개인정보\n"
    "ON_TOPIC: 한자·어휘·낱말 뜻·교과 용어 학습 질문\nOFF_TOPIC: 그 밖의 모든 주제\n라벨 한 단어만 출력."
)


class LLMClassifier:
    def __init__(self, provider: LLMProvider):
        self.provider = provider

    def classify(self, text: str) -> str:
        """Return HARMFUL / PII / ON_TOPIC / OFF_TOPIC; anything else or failure -> OFF_TOPIC."""
        try:
            out = self.provider.complete(_CLASSIFIER_SYSTEM, [{"role": "user", "content": text}])
        except LLMError:
            return "OFF_TOPIC"
        label = (out or "").strip().upper().split()[0:1]
        label = label[0].strip(".:") if label else ""
        return label if label in {"HARMFUL", "PII", "ON_TOPIC", "OFF_TOPIC"} else "OFF_TOPIC"


@dataclass(frozen=True)
class Gate:
    action: str            # "allow" | "block" | "redirect"
    category: Optional[str] = None  # block category, or "off_topic"
    source: str = "rule"


def rule_gate(text: str) -> Optional[Gate]:
    """Local, synchronous stage: a rule-based block, else None (the classifier stage decides)."""
    harm = detect_rule_harm(text)
    return Gate("block", harm, "rule") if harm else None


def gate_from_label(text: str, label: Optional[str], word_on_topic: bool = False) -> Gate:
    """Second stage for a message the rules did not block. label: the LLM classifier's label, or None when
    no classifier is configured (rules only). word_on_topic: the message is a short Hangul word question that
    우리말샘 resolved to a hanja word ("학교" -> 學校, chatbot.wordquery) - on-topic by rule like a message
    containing hanja, so OFF_TOPIC cannot redirect it; PII / HARMFUL still block."""
    on_topic = word_on_topic or rule_on_topic(text)
    if label is not None:
        if label == "PII":
            return Gate("block", "pii", "llm")
        if label == "HARMFUL":
            return Gate("block", "violence", "llm")
        if on_topic:
            return Gate("allow")
        return Gate("allow", source="llm") if label == "ON_TOPIC" else Gate("redirect", "off_topic", "llm")
    return Gate("allow") if on_topic else Gate("redirect", "off_topic", "rule")


def gate_message(text: str, classifier: Optional[LLMClassifier] = None, word_on_topic: bool = False) -> Gate:
    """rule_gate, then the classifier (if any). The streaming path runs the same two stages but overlaps
    the classifier call with answer generation (chatbot.streaming)."""
    return rule_gate(text) or gate_from_label(text, classifier.classify(text) if classifier is not None else None,
                                              word_on_topic)
