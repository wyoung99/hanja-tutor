"""Short Hangul word questions ("학교", "학교?", "학교 뜻", "학교가 뭐야") -> the word's hanja.

1. ``word_candidates``: is the message a short word question? -> the Hangul stems to look up
   (the word itself, then the word without a trailing particle: "학교가" -> "학교가", "학교").
2. ``resolve_entries``: 우리말샘 senses (``sense.origin`` = 원어) -> WordResolution.
   * general-vocabulary senses (cat == "") decide, in the API's own order: the first one whose 원어 is pure
     hanja of the same length wins (학교 -> 學校, 사과 -> 沙果, not 四科); the definition is that spelling's
     lowest-numbered sense; if general senses exist but none has a hanja 원어 the word is native
     (나무, 바보) and is NOT resolved to hanja (no fabrication);
   * without general senses, the remaining non-proper-name senses are used the same way;
   * proper names / works (지명, 책명, 영상, ...) and affixes ("-형") are ignored.
3. ``syllable_candidates``: fallback without a dictionary hit (or without an API key): per-syllable DB
   hanja whose 음 matches, in the child's body - listed, never combined into an invented word.

Only a word resolved to hanja (step 2) counts as on-topic for the safety gate.
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .dictionary import DictEntry
from .knowledge import HANJA_RE, HanjaKnowledge

MAX_HANGUL = 10
MAX_ALTERNATIVES = 2
PER_SYLLABLE = 3
LOOKUP_BUDGET_SECONDS = 2.0  # AC9: the lookup runs before the answer stream (overlapping the classifier)

_ALLOWED_RE = re.compile(r"^[가-힣\s?!.,~…？！。]+$")
_PUNCT_RE = re.compile(r"[?!.,~…？！。]+")
_TAIL_WORDS = {
    "뜻", "뜻은", "뜻이", "뜻이야", "뜻이에요", "뜻이예요", "뜻좀", "의미", "의미는", "의미가", "무슨", "무슨뜻",
    "뭐", "뭐야", "뭐예요", "뭐에요", "뭐니", "뭐지", "뭘까", "뭐임", "뭔데", "뭐요", "무엇", "무엇이야", "무엇인가요",
    "무엇이에요", "무엇일까", "알려줘", "알려주세요", "알려줄래", "가르쳐줘", "가르쳐주세요", "설명해줘", "설명해주세요",
    "한자", "한자로", "한자는", "한자가", "어떻게", "써", "써줘", "써요", "쓰는지", "쓰면", "쓰니", "쓰나요", "궁금해",
    "궁금해요", "이야", "야", "이란", "란", "좀",
}
_GLUED_TAIL_RE = re.compile(r"(이|가|은|는)?(뜻|의미)?(이|가|은|는)?(뭐야|뭐예요|뭐에요|뭐니|뭐지|뭘까|뭐임|뭐요|무엇이야|"
                            r"무엇인가요|무엇이에요)$|(의)?(뜻|의미)(이|은)?$")
_PARTICLES = ("이라는", "이라고", "이란", "라는", "라고", "에서", "으로", "이랑", "이야", "이에요", "예요", "란", "랑",
              "이", "가", "은", "는", "을", "를", "의", "도", "와", "과", "로", "에", "야", "요")
_PROPER_CATS = {"지명", "인명", "책명", "영상", "음악", "문학", "미술", "무용", "연극", "작품", "고유", "단체", "상품"}
_MARKS_RE = re.compile(r"[-^·\s]")

_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="urimalsaem")


@dataclass(frozen=True)
class WordResolution:
    word: str                     # Hangul stem as looked up ("학교")
    hanja: Optional[str]          # primary hanja spelling ("學校"); None = native word (no hanja 원어)
    alternatives: Tuple[str, ...] = ()  # other hanja spellings of the same sound (general senses)
    definition: str = ""          # 우리말샘 definition of the chosen sense

    @property
    def is_hanja(self) -> bool:
        return bool(self.hanja)


def word_candidates(message: str) -> List[str]:
    """Stems to look up when message is a short Hangul word question, else []."""
    text = (message or "").strip()
    if not text or HANJA_RE.search(text) or not _ALLOWED_RE.match(text):
        return []
    if len(re.findall("[가-힣]", text)) > MAX_HANGUL:
        return []
    words = [w for w in _PUNCT_RE.sub(" ", text).split() if w]
    while len(words) > 1 and words[-1] in _TAIL_WORDS:
        words.pop()
    if len(words) != 1:
        return []
    word = _GLUED_TAIL_RE.sub("", words[0]) or words[0]
    out = [word]
    for p in _PARTICLES:  # longest first: "학교에서" -> "학교"
        if word.endswith(p) and len(word) > len(p):
            stem = word[: -len(p)]
            if stem not in out:
                out.append(stem)
            if len(out) >= 3:
                break
    return out


def _hanja_spelling(entry: DictEntry, stem: str) -> Optional[str]:
    for variant in entry.origin.split("/"):
        v = _MARKS_RE.sub("", variant)
        if v and len(v) == len(stem) and len(HANJA_RE.findall(v)) == len(v):
            return v
    return None


def _sense_no(e: DictEntry) -> int:
    return int(e.sense_no) if e.sense_no.isdigit() else 999


def resolve_entries(stem: str, entries: Sequence[DictEntry]) -> Optional[WordResolution]:
    """WordResolution for stem from its 우리말샘 senses, or None when no sense matches the word exactly."""
    exact = [e for e in entries if not e.word.startswith("-") and _MARKS_RE.sub("", e.word) == stem
             and e.cat not in _PROPER_CATS]
    if not exact:
        return None
    general = [e for e in exact if not e.cat]  # API order = 우리말샘's own ranking (사과: 沙果 before 四科)
    pool = general or exact
    spelled = [(e, _hanja_spelling(e, stem)) for e in pool]
    hanja_senses = [(e, h) for e, h in spelled if h]
    if not hanja_senses:
        return WordResolution(stem, None, (), pool[0].definition)
    primary = hanja_senses[0][1]
    # the clearest definition of that spelling: lowest sense number among its non-proper senses
    # (학교: the API lists the slang sense 002 '교도소' before the basic sense 001)
    best = min((e for e in exact if _hanja_spelling(e, stem) == primary), key=_sense_no)
    alts: List[str] = []
    for _, h in hanja_senses:
        if h != primary and h not in alts:
            alts.append(h)
    return WordResolution(stem, primary, tuple(alts[:MAX_ALTERNATIVES]), best.definition)


def resolve_word(dictionary, message: str, budget: float = LOOKUP_BUDGET_SECONDS
                 ) -> Tuple[List[str], Optional[WordResolution], bool]:
    """(candidates, resolution, looked_up). looked_up is False when there was nothing to ask (not a word query,
    no dictionary, no ``search`` support, or every lookup failed)."""
    cands = word_candidates(message)
    search = getattr(dictionary, "search", None) if dictionary is not None else None
    if not cands or search is None:
        return cands, None, False
    # concurrent lookups (cached in the client) keep the latency of one call; a slow dictionary is abandoned
    # after `budget` seconds (the request continues without it, the late result still fills the cache)
    futures = [_POOL.submit(search, c) for c in cands]
    end = time.monotonic() + budget
    results = []
    for f in futures:
        try:
            results.append(f.result(timeout=max(0.0, end - time.monotonic())))
        except Exception:  # noqa: BLE001 - timeout / client error == no answer from the dictionary
            results.append(None)
    looked_up = False
    for stem, entries in zip(cands, results):
        if entries is None:
            continue
        looked_up = True
        res = resolve_entries(stem, entries)
        if res is not None:
            return cands, res, True
    return cands, None, looked_up


def lookup_many(dictionary, terms: Sequence[str]) -> dict:
    """dictionary.lookup for several terms at once (hanja missing from the DB): one round trip of latency."""
    if not terms:
        return {}
    if len(terms) == 1:
        return {terms[0]: dictionary.lookup(terms[0])}
    return dict(zip(terms, _POOL.map(dictionary.lookup, terms)))


def syllable_candidates(kb: HanjaKnowledge, target: Optional[tuple], stem: str) -> Optional[List[Tuple[str, List[str]]]]:
    """[(syllable, [hanja...])] when every syllable has DB hanja with that 음 in the child's body, else None.

    Target-grade hanja first, then the rest of the body in grade order; at most PER_SYLLABLE each."""
    if not target or not stem or len(stem) > 4:
        return None
    body, grade = target[0], target[1]
    out = []
    for syl in stem:
        chars = kb.chars_with_eum(body, syl)
        if not chars:
            return None
        chars.sort(key=lambda c: (not kb.within_target(body, grade, c), kb.grade_rank(body, c), c))
        out.append((syl, chars[:PER_SYLLABLE]))
    return out
