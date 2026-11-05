"""Question model, DB-grounded generation, and DB cross-check (verify) used as the pre-exam gate.

Types: eum (한자->음), hun_eum (한자->훈음), hun_to_hanja (훈음->한자 선택),
writing (훈음 -> 빈 칸에 직접 쓰기; only chars with stroke data that are not flagged exclude_from_grading),
busu (한자 -> 부수 선택; radical from the 한국어문회 list, distractors are other DB radicals),
pilsun (표시한 획이 몇 번째 획인지 선택; stroke order from hanzi-writer data, only chars whose stroke count
equals the official total_strokes. Korean-standard stroke order is NOT verified, see PILSUN_DATA_CAVEAT).
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

HANJA_RE = re.compile("[一-鿿㐀-䶿豈-﫿]")


PILSUN_MIN_STROKES = 4  # 4 distinct ordinal options
PILSUN_DATA_CAVEAT = ("筆順 문항의 획순은 hanzi-writer(Make Me a Hanzi) 데이터 기준이며, 한국 표준 획순과의 대조는 "
                      "검증에서 제외됨(AC7, 2026-11-05 사용자 결정). 획 수가 한국어문회 total_strokes와 같은 글자만 출제.")


class ExamUnavailable(Exception):
    pass


@dataclass(frozen=True)
class Hanja:
    char: str
    hun: str
    eum: str
    radical: str = ""       # 部首 from the body's official list (한국어문회 CSV); "" = unknown
    total_strokes: int = 0  # official total stroke count (한국어문회 CSV); 0 = unknown

    @property
    def hun_eum(self) -> str:
        return f"{self.hun} {self.eum}"


@dataclass(frozen=True)
class Question:
    id: str
    type: str
    points: int
    prompt: str
    char: str  # the hanja this question is about (answer char for hun_to_hanja/writing)
    options: Tuple[str, ...]
    answer: str  # option text for choice types; the char for writing
    explanation: str
    source: str = "db"  # "db" | "ai"
    slot: str = ""  # official type this question fills (e.g. "dokeum"); "" = same as `type`
    stroke_index: int = -1  # pilsun: 0-based index of the highlighted stroke


def ordinal(i: int) -> str:
    """0-based stroke index -> option text ("1번째")."""
    return f"{i + 1}번째"


def pilsun_ok(h: Hanja, strokes) -> bool:
    """Char may be asked as 筆順: gradable stroke data, >= 4 strokes, count equals the official total_strokes."""
    d = (strokes or {}).get(h.char)
    return bool(d and not d.exclude_from_grading and d.stroke_count >= PILSUN_MIN_STROKES
                and h.total_strokes == d.stroke_count)


def explanation_for(h: Hanja) -> str:
    return f"'{h.char}'의 훈음은 '{h.hun_eum}'입니다."


def _prompt(qtype: str, h: Hanja) -> str:
    if qtype == "eum":
        return f"다음 한자의 음(소리)은? {h.char}"
    if qtype == "hun_eum":
        return f"다음 한자의 훈(뜻)과 음(소리)은? {h.char}"
    if qtype == "hun_to_hanja":
        return f"'{h.hun_eum}'에 해당하는 한자는?"
    if qtype == "busu":
        return f"다음 한자의 부수는? {h.char}"
    if qtype == "pilsun":
        return f"다음 한자에서 빨간색으로 표시한 획은 몇 번째로 쓰나요? {h.char}"
    return f"'{h.hun_eum}'에 해당하는 한자를 빈 칸에 직접 쓰세요."


def build_question(qid: str, qtype: str, points: int, h: Hanja, pool: Sequence[Hanja], rng: random.Random,
                   strokes=None, slot: str = "") -> Question:
    if qtype == "writing":
        return Question(qid, qtype, points, _prompt(qtype, h), h.char, (), h.char, explanation_for(h), slot=slot)
    if qtype == "pilsun":
        if not pilsun_ok(h, strokes):
            raise ExamUnavailable(f"no verifiable stroke-order data for {h.char!r}")
        n = strokes[h.char].stroke_count
        idx = rng.randrange(n)
        others = rng.sample([i for i in range(n) if i != idx], 3)
        options = tuple(ordinal(i) for i in sorted([idx] + others))
        expl = f"'{h.char}'에서 표시한 획은 {ordinal(idx)}로 씁니다(총 {n}획). " + explanation_for(h)
        return Question(qid, qtype, points, _prompt(qtype, h), h.char, options, ordinal(idx), expl,
                        slot=slot, stroke_index=idx)
    if qtype == "busu":
        if not h.radical:
            raise ExamUnavailable(f"no radical for {h.char!r}")
        rads = sorted({x.radical for x in pool if x.radical and x.radical != h.radical})
        if len(rads) < 3:
            raise ExamUnavailable("pool too small to build 4 distinct radical options")
        options = [h.radical] + rng.sample(rads, 3)
        rng.shuffle(options)
        expl = f"'{h.char}'의 부수는 '{h.radical}'입니다. " + explanation_for(h)
        return Question(qid, qtype, points, _prompt(qtype, h), h.char, tuple(options), h.radical, expl, slot=slot)
    if qtype == "eum":
        key, correct = (lambda x: x.eum), h.eum
    elif qtype == "hun_eum":
        key, correct = (lambda x: x.hun_eum), h.hun_eum
    elif qtype == "hun_to_hanja":
        key, correct = (lambda x: x.char), h.char
    else:
        raise ExamUnavailable(f"unknown type {qtype}")
    # distractors must differ in the *answer value* and never be an equivalent reading of the answer
    cands: Dict[str, Hanja] = {}
    for x in pool:
        if x.char == h.char or x.hun_eum == h.hun_eum:
            continue
        if qtype == "eum" and x.eum == h.eum:
            continue
        cands.setdefault(key(x), x)
    if len(cands) < 3:
        raise ExamUnavailable("pool too small to build 4 distinct options")
    options = [correct] + rng.sample(sorted(cands), 3)
    rng.shuffle(options)
    return Question(qid, qtype, points, _prompt(qtype, h), h.char, tuple(options), correct, explanation_for(h),
                    slot=slot)


_READING_HANJA_CACHE: Dict[int, Tuple[object, frozenset]] = {}


def _reading_hanja(db: Dict[str, Hanja]) -> frozenset:
    """Hanja that occur inside DB 훈·음 strings (source annotations such as 方 = '모[棱] 방').

    They are DB-grounded text, so a question quoting that reading is not inventing a hanja."""
    hit = _READING_HANJA_CACHE.get(id(db))
    if hit is not None and hit[0] is db:
        return hit[1]
    out = frozenset(ch for h in db.values() for ch in HANJA_RE.findall(h.hun + h.eum + h.radical))
    _READING_HANJA_CACHE[id(db)] = (db, out)
    return out


def verify_question(q: Question, db: Dict[str, Hanja], strokes=None) -> List[str]:
    """Independent DB cross-check. Returns problems ([] => OK to ask).

    `strokes` (char -> StrokeData) is needed to check 筆順 questions; without it they fail."""
    probs: List[str] = []
    h = db.get(q.char)
    if h is None:
        return [f"{q.id}: char {q.char!r} not in DB"]
    for ch in set(HANJA_RE.findall(q.prompt + "".join(q.options) + q.explanation + q.answer)):
        if ch not in db and ch not in _reading_hanja(db):  # e.g. 方 '모[棱]': hanja inside a DB 훈 itself
            probs.append(f"{q.id}: hanja {ch!r} not in DB")
    if h.hun_eum not in q.explanation:
        probs.append(f"{q.id}: explanation lacks DB 훈음 {h.hun_eum!r}")
    if q.type == "writing":
        if q.answer != h.char:
            probs.append(f"{q.id}: writing answer != DB char")
        if h.hun_eum not in q.prompt:
            probs.append(f"{q.id}: writing prompt 훈음 differs from DB")
        return probs
    if len(set(q.options)) != len(q.options) or len(q.options) < 2:
        probs.append(f"{q.id}: options not distinct")
    if q.answer not in q.options:
        probs.append(f"{q.id}: answer not among options")
    if q.type == "eum":
        if q.answer != h.eum:
            probs.append(f"{q.id}: answer {q.answer!r} != DB 음 {h.eum!r}")
        if q.char not in q.prompt:
            probs.append(f"{q.id}: prompt char mismatch")
        if sum(1 for o in q.options if o == h.eum) != 1:
            probs.append(f"{q.id}: correct 음 must appear exactly once")
    elif q.type == "hun_eum":
        if q.answer != h.hun_eum:
            probs.append(f"{q.id}: answer {q.answer!r} != DB 훈음 {h.hun_eum!r}")
        if q.char not in q.prompt:
            probs.append(f"{q.id}: prompt char mismatch")
        if sum(1 for o in q.options if o == h.hun_eum) != 1:
            probs.append(f"{q.id}: correct 훈음 must appear exactly once")
    elif q.type == "busu":
        if not h.radical or q.answer != h.radical:
            probs.append(f"{q.id}: answer {q.answer!r} != DB 부수 {h.radical!r}")
        if q.char not in q.prompt:
            probs.append(f"{q.id}: prompt char mismatch")
        radicals = {x.radical for x in db.values() if x.radical}
        if any(o not in radicals for o in q.options):
            probs.append(f"{q.id}: option is not a DB radical")
        if sum(1 for o in q.options if o == h.radical) != 1:
            probs.append(f"{q.id}: correct 부수 must appear exactly once")
    elif q.type == "pilsun":
        d = (strokes or {}).get(q.char)
        if d is None:
            probs.append(f"{q.id}: no stroke data to verify 筆順")
        else:
            n = d.stroke_count
            if not pilsun_ok(h, strokes):
                probs.append(f"{q.id}: stroke data not usable (excluded, < {PILSUN_MIN_STROKES} strokes, or "
                             f"count {n} != official {h.total_strokes})")
            if not 0 <= q.stroke_index < n or q.answer != ordinal(q.stroke_index):
                probs.append(f"{q.id}: answer {q.answer!r} != highlighted stroke {q.stroke_index}")
            if any(o not in {ordinal(i) for i in range(n)} for o in q.options):
                probs.append(f"{q.id}: option outside 1..{n}")
        if q.char not in q.prompt:
            probs.append(f"{q.id}: prompt char mismatch")
    elif q.type == "hun_to_hanja":
        if q.answer != h.char:
            probs.append(f"{q.id}: answer != DB char")
        if h.hun_eum not in q.prompt:
            probs.append(f"{q.id}: prompt 훈음 differs from DB")
        matching = [o for o in q.options if o in db and db[o].hun_eum == h.hun_eum]
        if matching != [h.char]:
            probs.append(f"{q.id}: options must contain exactly one char with the prompt 훈음")
    else:
        probs.append(f"{q.id}: unknown type {q.type}")
    return probs


def filter_questions(questions: Iterable[Question], db: Dict[str, Hanja],
                     strokes=None) -> Tuple[List[Question], List[Tuple[Question, List[str]]]]:
    """Gate for any (incl. AI-generated) question: mismatching ones are discarded, never asked."""
    kept: List[Question] = []
    dropped: List[Tuple[Question, List[str]]] = []
    for q in questions:
        p = verify_question(q, db, strokes)
        (dropped.append((q, p)) if p else kept.append(q))
    return kept, dropped


def audit_error_rate(sample: Sequence[Question], db: Dict[str, Hanja], strokes=None) -> Tuple[int, int, float]:
    """(checked, erroneous, rate) over a sample; used for the >=200 question review."""
    bad = sum(1 for q in sample if verify_question(q, db, strokes))
    return len(sample), bad, (bad / len(sample) if sample else 0.0)


AI_SYSTEM = (
    "너는 초등학생 한자 급수 시험 출제자다. 사용자가 'X: 훈 A, 음 B' 형식으로 준 값만 정답 근거로 쓴다. "
    "설명 없이 JSON 배열만 출력한다. 각 원소: "
    '{"type":"eum|hun_eum|hun_to_hanja","char":"X","options":[보기 4개],"answer":"...","explanation":"...",'
    '"prompt":"(hun_to_hanja만) 문제 문장"}. '
    "규칙: char는 항상 문제의 대상 한자 X 한 글자다. eum의 정답은 B, hun_eum의 정답은 'A B'(훈과 음 사이 공백 하나), "
    "hun_to_hanja는 prompt에 'A B'를 그대로 넣고 보기는 한자 한 글자씩이며 정답은 X다. 보기 4개는 서로 다르고 정답은 "
    "정확히 하나다. explanation에는 반드시 'A B'를 글자 그대로 포함한다. 보기와 해설에는 목록에 있는 한자만 쓴다. "
    "한자마다 문항 1개."
)


def parse_ai_questions(text: str, points: int = 1, start: int = 1) -> List[Question]:
    """Parse LLM output into Questions (source='ai'). Malformed items are skipped, never trusted."""
    import json

    m = re.search(r"\[.*\]", text or "", re.S)
    if not m:
        return []
    try:
        items = json.loads(m.group(0))
    except ValueError:
        return []
    out: List[Question] = []
    for i, it in enumerate(items if isinstance(items, list) else []):
        try:
            qtype, char = str(it["type"]), str(it["char"])
            opts = tuple(str(o) for o in it["options"])
            h = Hanja(char, "", "")
            prompt = {"eum": f"다음 한자의 음(소리)은? {char}",
                      "hun_eum": f"다음 한자의 훈(뜻)과 음(소리)은? {char}"}.get(qtype, str(it.get("prompt", "")))
            out.append(Question(f"ai{start + i}", qtype, points, prompt, char, opts,
                                str(it["answer"]), str(it["explanation"]), source="ai"))
        except (KeyError, TypeError):
            continue
    return out


def generate_ai_questions(llm, chars: Sequence[str], db: Dict[str, Hanja], points: int = 1) -> Tuple[List[Question], List[Tuple[Question, List[str]]]]:
    """Ask an LLMProvider for questions about `chars`, then DB-verify; mismatches are discarded.

    Pass the reasoning-tier provider: ``llm_router.for_task("question_generation")`` (Sonnet).
    """
    from llm import LLMError

    listing = "\n".join(f"{c}: 훈 {db[c].hun}, 음 {db[c].eum}" for c in chars if c in db)
    try:
        text = llm.complete(AI_SYSTEM, [{"role": "user", "content": listing}])
    except LLMError:
        return [], []
    return filter_questions(parse_ai_questions(text, points), db)


def generate_questions(
    fmt, pool: Sequence[Hanja], writing_chars: Sequence[str], rng: random.Random,
    db: Optional[Dict[str, Hanja]] = None, allow_substitute_writing: bool = False, strokes=None,
) -> List[Question]:
    """Questions in exactly the format's slots (counts/types/points), in slot order.

    Writing only from `writing_chars`; 筆順 only from chars passing `pilsun_ok` (needs `strokes`); 部首 only
    from chars with a radical. Scarce types pick their chars first; a char is reused only when the pool
    is exhausted."""
    by_char = {h.char: h for h in pool}
    db = db or by_char
    wset = set(c for c in writing_chars if c in by_char)
    eligible = {"writing": lambda c: c in wset, "pilsun": lambda c: pilsun_ok(by_char[c], strokes),
                "busu": lambda c: bool(by_char[c].radical)}
    order = sorted(by_char)
    rng.shuffle(order)
    used: set = set()
    picks: Dict[int, List[Tuple[str, str]]] = {}  # spec index -> [(qtype, char)]
    specs = list(fmt.types)
    for si in sorted(range(len(specs)), key=lambda i: specs[i].type not in eligible):
        spec = specs[si]
        ok = eligible.get(spec.type)
        if ok is None:
            seq = [c for c in order if c not in used] + [c for c in order if c in used]
            chosen = [(spec.type, seq[i % len(seq)]) for i in range(spec.count)]
        else:
            cands = [c for c in order if ok(c)]
            cands = [c for c in cands if c not in used] + [c for c in cands if c in used]
            if len(cands) >= spec.count:
                chosen = [(spec.type, c) for c in cands[: spec.count]]
            elif spec.type == "writing" and allow_substitute_writing:  # opt-in: too few gradable chars
                rest = [c for c in order if c not in used and c not in cands] + order
                chosen = [("writing", c) for c in cands] + [
                    ("hun_to_hanja", rest[i % len(rest)]) for i in range(spec.count - len(cands))]
            else:
                what = {"writing": "gradable (stroke-data) chars", "pilsun": "chars with verifiable stroke order",
                        "busu": "chars with a radical"}[spec.type]
                raise ExamUnavailable(f"{spec.type} needs {spec.count} {what} in the pool, only {len(cands)} available")
        used |= {c for _, c in chosen}
        picks[si] = chosen
    out: List[Question] = []
    n = 0
    for si, spec in enumerate(specs):
        for qtype, char in picks[si]:
            n += 1
            q = build_question(f"q{n}", qtype, spec.points, by_char[char], pool, rng, strokes, spec.key)
            problems = verify_question(q, db, strokes)
            if problems:  # defensive gate; should never trigger for DB-built questions
                raise ExamUnavailable(f"generated question failed DB verification: {problems}")
            out.append(q)
    return out
