"""Exam format config loader (독립된 설정 데이터: data/exam_formats.json)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple

DATA = Path(__file__).resolve().parents[2] / "data"
TYPES = ("eum", "hun_eum", "hun_to_hanja", "writing")


class FormatError(Exception):
    pass


@dataclass(frozen=True)
class TypeSpec:
    type: str
    count: int
    points: int


@dataclass(frozen=True)
class ExamFormat:
    body: str
    grade: str
    minutes: int
    pass_score: int
    types: Tuple[TypeSpec, ...]
    verified: bool = False

    @property
    def question_count(self) -> int:
        return sum(t.count for t in self.types)

    @property
    def total_score(self) -> int:
        return sum(t.count * t.points for t in self.types)

    @property
    def has_writing(self) -> bool:
        return any(t.type == "writing" and t.count > 0 for t in self.types)


FORMAT_PENDING_MESSAGE = "형식 준비 중"


def load_pending(path: Path = DATA / "exam_formats.json") -> Dict[Tuple[str, str], str]:
    """(body, grade) -> reason, for supported grades whose official exam format is not known yet."""
    raw = json.loads(path.read_text(encoding="utf-8")).get("format_pending") or {}
    return {(body, g): str(reason) for body, grades in raw.items() for g, reason in grades.items()}


def exam_status(formats: Dict[Tuple[str, str], "ExamFormat"], pending: Dict[Tuple[str, str], str],
                body: str, grade: str) -> str:
    """'available' | 'format_pending' | 'unsupported'."""
    if (body, grade) in formats:
        return "available"
    if (body, grade) in pending:
        return "format_pending"
    return "unsupported"


def load_formats(path: Path = DATA / "exam_formats.json") -> Dict[Tuple[str, str], ExamFormat]:
    raw = json.loads(path.read_text(encoding="utf-8"))["bodies"]
    out: Dict[Tuple[str, str], ExamFormat] = {}
    for body, grades in raw.items():
        for grade, v in grades.items():
            specs = tuple(TypeSpec(t["type"], int(t["count"]), int(t["points"])) for t in v["types"])
            for s in specs:
                if s.type not in TYPES:
                    raise FormatError(f"{body}/{grade}: unknown type {s.type}")
            f = ExamFormat(body, grade, int(v["minutes"]), int(v["pass_score"]), specs, bool(v.get("verified", False)))
            if not 0 < f.pass_score <= f.total_score:
                raise FormatError(f"{body}/{grade}: pass_score outside 1..{f.total_score}")
            out[(body, grade)] = f
    overlap = set(out) & set(load_pending(path))
    if overlap:
        raise FormatError(f"grades both formatted and format_pending: {sorted(overlap)}")
    return out
