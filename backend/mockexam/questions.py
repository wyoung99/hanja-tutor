"""Question model, DB-grounded generation, and DB cross-check (verify) used as the pre-exam gate.

Types: eum (한자->음), hun_eum (한자->훈음), hun_to_hanja (훈음->한자 선택),
writing (훈음 -> 빈 칸에 직접 쓰기; only chars with stroke data that are not flagged exclude_from_grading).
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

HANJA_RE = re.compile("[一-鿿㐀-䶿豈-﫿]")


class ExamUnavailable(Exception):
    pass


@dataclass(frozen=True)
class Hanja:
    char: str
    hun: str
    eum: str

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


def explanation_for(h: Hanja) -> str:
    return f"'{h.char}'의 훈음은 '{h.hun_eum}'입니다."


def _prompt(qtype: str, h: Hanja) -> str:
    if qtype == "eum":
        return f"다음 한자의 음(소리)은? {h.char}"
    if qtype == "hun_eum":
        return f"다음 한자의 훈(뜻)과 음(소리)은? {h.char}"
    if qtype == "hun_to_hanja":
        return f"'{h.hun_eum}'에 해당하는 한자는?"
    return f"'{h.hun_eum}'에 해당하는 한자를 빈 칸에 직접 쓰세요."


def build_question(qid: str, qtype: str, points: int, h: Hanja, pool: Sequence[Hanja], rng: random.Random) -> Question:
    if qtype == "writing":
        return Question(qid, qtype, points, _prompt(qtype, h), h.char, (), h.char, explanation_for(h))
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
    return Question(qid, qtype, points, _prompt(qtype, h), h.char, tuple(options), correct, explanation_for(h))


_READING_HANJA_CACHE: Dict[int, Tuple[object, frozenset]] = {}


def _reading_hanja(db: Dict[str, Hanja]) -> frozenset:
    """Hanja that occur inside DB 훈·음 strings (source annotations such as 方 = '모[棱] 방').

    They are DB-grounded text, so a question quoting that reading is not inventing a hanja."""
    hit = _READING_HANJA_CACHE.get(id(db))
    if hit is not None and hit[0] is db:
        return hit[1]
    out = frozenset(ch for h in db.values() for ch in HANJA_RE.findall(h.hun + h.eum))
    _READING_HANJA_CACHE[id(db)] = (db, out)
    return out


def verify_question(q: Question, db: Dict[str, Hanja]) -> List[str]:
    """Independent DB cross-check. Returns problems ([] => OK to ask)."""
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


def filter_questions(questions: Iterable[Question], db: Dict[str, Hanja]) -> Tuple[List[Question], List[Tuple[Question, List[str]]]]:
    """Gate for any (incl. AI-generated) question: mismatching ones are discarded, never asked."""
    kept: List[Question] = []
    dropped: List[Tuple[Question, List[str]]] = []
    for q in questions:
        p = verify_question(q, db)
        (dropped.append((q, p)) if p else kept.append(q))
    return kept, dropped


def audit_error_rate(sample: Sequence[Question], db: Dict[str, Hanja]) -> Tuple[int, int, float]:
    """(checked, erroneous, rate) over a sample; used for the >=200 question review."""
    bad = sum(1 for q in sample if verify_question(q, db))
    return len(sample), bad, (bad / len(sample) if sample else 0.0)


AI_SYSTEM = (
    "너는 한자 시험 출제자다. 주어진 한자들에 대해 JSON 배열만 출력한다. 각 원소: "
    '{"type":"eum|hun_eum|hun_to_hanja","char":"한자","options":[...],"answer":"...","explanation":"..."}'
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
    db: Optional[Dict[str, Hanja]] = None, allow_substitute_writing: bool = False,
) -> List[Question]:
    """Questions in exactly the format's counts/types/points. Writing only from `writing_chars`."""
    by_char = {h.char: h for h in pool}
    wset = [c for c in writing_chars if c in by_char]
    db = db or by_char
    writing_need = sum(t.count for t in fmt.types if t.type == "writing")
    if len(wset) < writing_need and not allow_substitute_writing:
        raise ExamUnavailable(
            f"writing needs {writing_need} gradable (stroke-data) chars in the pool, only {len(wset)} available")
    order = sorted(by_char)
    rng.shuffle(order)
    w_order = list(wset)
    rng.shuffle(w_order)
    w_used = set(w_order[:writing_need])
    seq = [c for c in order if c not in w_used] + [c for c in order if c in w_used]
    out: List[Question] = []
    pos = 0
    n = 0
    for spec in fmt.types:
        for i in range(spec.count):
            qtype = spec.type
            if qtype == "writing" and i < len(w_order):
                char = w_order[i]
            else:
                char = seq[pos % len(seq)]
                pos += 1
                if qtype == "writing":  # opt-in substitution when too few gradable chars
                    qtype = "hun_to_hanja"
            n += 1
            q = build_question(f"q{n}", qtype, spec.points, by_char[char], pool, rng)
            problems = verify_question(q, db)
            if problems:  # defensive gate; should never trigger for DB-built questions
                raise ExamUnavailable(f"generated question failed DB verification: {problems}")
            out.append(q)
    return out
