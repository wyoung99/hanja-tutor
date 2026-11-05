"""Per-exam-body hanja store used by the running app (the authority for 훈·음 and assignments).

Source of truth: ``data/hanja_db/hanja_by_body.csv`` (body,grade,char,hun,eum,reviewed,char_nfc,source_raw)
and ``data/hanja_db/assignments.csv`` (body,grade,char). 훈·음 are kept per (body, char): the same
character can have different readings per body (e.g. 兄: korea_eomunhoe "형 형", daegeom "맏 형"),
and values are never mixed across bodies.

Lookups are NFC-normalised: the official files use CJK compatibility code points (金 U+F90A, 六 U+F9D1 ...),
while user input, URLs and stroke data use the unified ideograph. ``reviewed`` is reported as stored;
it is never set here (human review is pending, see data/SOURCES.md).
"""
from __future__ import annotations

import csv
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s or "")


@dataclass(frozen=True)
class Reading:
    body: str
    grade: str
    char: str          # NFC
    hun: str
    eum: str
    reviewed: bool
    source_raw: str = ""
    char_raw: str = ""  # code point as in the official file

    @property
    def complete(self) -> bool:
        return bool(self.hun.strip() and self.eum.strip())

    @property
    def hun_eum(self) -> str:
        return f"{self.hun} {self.eum}"


@dataclass
class HanjaStore:
    bodies: Dict[str, List[str]]                                  # body -> grades, easiest..hardest
    assignments: Dict[Tuple[str, str], List[str]]                 # (body, grade) -> NFC chars, file order
    readings: Dict[Tuple[str, str], Reading] = field(default_factory=dict)  # (body, NFC char) -> reading

    # ---- loading -------------------------------------------------------------
    @classmethod
    def from_rows(cls, bodies: Dict[str, List[str]], assignment_rows: Iterable[dict],
                  reading_rows: Iterable[dict]) -> "HanjaStore":
        assign: Dict[Tuple[str, str], List[str]] = {}
        for r in assignment_rows:
            lst = assign.setdefault((r["body"], r["grade"]), [])
            c = nfc(r["char"])
            if c not in lst:
                lst.append(c)
        readings: Dict[Tuple[str, str], Reading] = {}
        for r in reading_rows:
            c = nfc(r.get("char_nfc") or r["char"])
            readings[(r["body"], c)] = Reading(
                r["body"], r["grade"], c, (r.get("hun") or "").strip(), (r.get("eum") or "").strip(),
                (r.get("reviewed") or "").strip() == "1", r.get("source_raw") or "", r["char"])
        return cls({b: list(g) for b, g in bodies.items()}, assign, readings)

    @classmethod
    def load(cls, data_dir: Path = DATA) -> "HanjaStore":
        from .verify import load_exam_bodies

        def rows(name: str) -> List[dict]:
            with (data_dir / "hanja_db" / name).open(encoding="utf-8", newline="") as fh:
                return list(csv.DictReader(fh))

        return cls.from_rows(load_exam_bodies(data_dir / "exam_bodies.json"), rows("assignments.csv"),
                             rows("hanja_by_body.csv"))

    # ---- queries -------------------------------------------------------------
    def reading(self, body: str, char: str) -> Optional[Reading]:
        """The body's own reading of `char` (None if the body does not have it). Never cross-body."""
        return self.readings.get((body, nfc(char)))

    def grade_chars(self, body: str, grade: str) -> List[str]:
        """Chars newly assigned at exactly this grade, in source order."""
        return list(self.assignments.get((body, grade), ()))

    def cumulative_chars(self, body: str, grade: str) -> List[str]:
        order = self.bodies.get(body, [])
        out: List[str] = []
        if grade in order:
            for g in order[: order.index(grade) + 1]:
                out += [c for c in self.assignments.get((body, g), ()) if c not in out]
        return out

    def grade_of(self, body: str, char: str) -> Optional[str]:
        c = nfc(char)
        for g in self.bodies.get(body, []):
            if c in self.assignments.get((body, g), ()):
                return g
        return None

    def all_chars(self) -> List[str]:
        seen: Dict[str, None] = {}
        for chars in self.assignments.values():
            for c in chars:
                seen.setdefault(c, None)
        return list(seen)

    def assignment_sets(self) -> Dict[Tuple[str, str], set]:
        return {k: set(v) for k, v in self.assignments.items()}

    def knowledge_rows(self) -> Dict[str, Dict[str, dict]]:
        """body -> char -> {"char","hun","eum","reviewed"} (for chatbot.knowledge.HanjaKnowledge)."""
        out: Dict[str, Dict[str, dict]] = {}
        for (body, c), r in self.readings.items():
            out.setdefault(body, {})[c] = {"char": c, "hun": r.hun, "eum": r.eum,
                                          "reviewed": "1" if r.reviewed else "0"}
        return out


EOMUNHOE_CSV = DATA / "official_lists" / "korea_eomunhoe" / "eomunhoe-hanja.csv"


def load_eomunhoe_attributes(path: Path = EOMUNHOE_CSV) -> Dict[str, Tuple[str, int]]:
    """NFC char -> (部首, total stroke count) from the 한국어문회 list (columns radical, total_strokes).

    Used for 한국어문회 部首 / 筆順 questions only (never applied to another body's readings)."""
    out: Dict[str, Tuple[str, int]] = {}
    if not path.exists():
        return out
    with path.open(encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            rad, tot = (r.get("radical") or "").strip(), (r.get("total_strokes") or "").strip()
            if rad and tot.isdigit():
                out.setdefault(nfc(r["hanja"]), (nfc(rad), int(tot)))
    return out
