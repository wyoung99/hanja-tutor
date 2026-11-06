"""StrokeData: hanzi-writer (Make Me a Hanzi) stroke medians with a per-char grading-exclusion flag.

Coordinates use the app's 0..1000 screen space (y down). Each stroke is a median polyline converted
from hanzi-writer-data by ``tracing/build_stroke_data.py`` (source/version/license in data/SOURCES.md).

Decision (2026-11-05, AC7 verify_exemption_reason): the data is used as-is. Matching it against a
Korean-standard stroke order is excluded from verification. A character is *gradable* (tracing is
graded, and it may appear as a mock-exam writing question) iff it has stroke data and its
``exclude_from_grading`` flag is not set. The flag is the slot for later marking characters whose
hanzi-writer order differs from the Korean standard. Characters without stroke data are excluded.
"""
from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Tuple

Point = Tuple[float, float]
DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "stroke_data"
KOREAN_STANDARD_EXEMPTION = "verification excluded by user decision 2026-11-05"


@dataclass(frozen=True)
class StrokeData:
    char: str
    strokes: Tuple[Tuple[Point, ...], ...]  # ordered; each a median polyline
    exclude_from_grading: bool = False

    @property
    def stroke_count(self) -> int:
        return len(self.strokes)


def _parse(raw: dict) -> Dict[str, StrokeData]:
    chars = raw.get("chars", raw)
    out: Dict[str, StrokeData] = {}
    for ch, v in chars.items():
        if ch.startswith("_"):
            continue
        c = unicodedata.normalize("NFC", ch)
        strokes = tuple(tuple((float(x), float(y)) for x, y in s) for s in v["strokes"])
        out[c] = StrokeData(c, strokes, bool(v.get("exclude_from_grading", False)))
    return out


def load_stroke_file(path: Path) -> Dict[str, StrokeData]:
    if not path.exists():
        return {}
    return _parse(json.loads(path.read_text(encoding="utf-8")))


def load_source(path: Path = DATA_DIR / "strokes.json") -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("_source", {})


def load_db() -> Dict[str, StrokeData]:
    """hanzi-writer-derived stroke data for every assigned char that has it (NFC keys)."""
    return load_stroke_file(DATA_DIR / "strokes.json")


def gradable_chars(db: Mapping[str, StrokeData]) -> List[str]:
    """Chars with stroke data and no exclusion flag: the only chars graded or used for writing questions."""
    return sorted(c for c, d in db.items() if d.stroke_count and not d.exclude_from_grading)


def is_gradable(char: str, db: Mapping[str, StrokeData]) -> bool:
    d = db.get(unicodedata.normalize("NFC", char))
    return bool(d and d.stroke_count and not d.exclude_from_grading)
