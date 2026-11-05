"""DB view used by the chatbot (the only authority for 훈·음 and grade).

훈·음 are looked up per exam body (the child's selected body): the same char may read differently per
body (兄: 어문회 "형 형", 검정회 "맏 형"), and values are never borrowed from another body.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional

HANJA_CLASS = "㐀-䶿一-鿿豈-﫿"
HANJA_RE = re.compile("[" + HANJA_CLASS + "]")


@dataclass
class HanjaKnowledge:
    hanja: Dict[str, Dict[str, dict]]           # body -> char(NFC) -> {"hun","eum","reviewed",...}
    assignments: Dict[tuple, set]               # (body, grade) -> chars
    grade_order: Dict[str, List[str]]           # body -> grades easiest..hardest
    vocab: Dict[str, str] = field(default_factory=dict)  # 한자어 -> 뜻풀이

    @classmethod
    def from_files(cls) -> "HanjaKnowledge":
        from hanja_db.store import HanjaStore

        return cls.from_store(HanjaStore.load())

    @classmethod
    def from_store(cls, store) -> "HanjaKnowledge":
        return cls(store.knowledge_rows(), store.assignment_sets(), {b: list(g) for b, g in store.bodies.items()})

    def entry(self, ch: str, body: Optional[str]) -> Optional[dict]:
        """The selected body's 훈·음 for ch, or None (not in that body's DB / incomplete / no body)."""
        if not body:
            return None
        row = self.hanja.get(body, {}).get(unicodedata.normalize("NFC", ch))
        if row and (row.get("hun") or "").strip() and (row.get("eum") or "").strip():
            return row
        return None

    def grade_of(self, body: str, ch: str) -> Optional[str]:
        """Lowest (easiest) grade at which ch is assigned for body."""
        ch = unicodedata.normalize("NFC", ch)
        for g in self.grade_order.get(body, []):
            if ch in self.assignments.get((body, g), ()):
                return g
        return None

    def within_target(self, body: str, grade: str, ch: str) -> bool:
        order = self.grade_order.get(body, [])
        g = self.grade_of(body, ch)
        return g is not None and grade in order and order.index(g) <= order.index(grade)

    def target_chars(self, body: str, grade: str) -> List[str]:
        order = self.grade_order.get(body, [])
        out: List[str] = []
        if grade in order:
            for g in order[: order.index(grade) + 1]:
                out += sorted(self.assignments.get((body, g), ()))
        return out

    def suggest_words(self, body: str, grade: str, n: int = 1) -> List[str]:
        """Hanja words made only of target-grade chars (for steering off-topic chats)."""
        ok = [w for w in self.vocab if all(self.within_target(body, grade, c) for c in HANJA_RE.findall(w))]
        if ok:
            return sorted(ok)[:n]
        return self.target_chars(body, grade)[:n]
