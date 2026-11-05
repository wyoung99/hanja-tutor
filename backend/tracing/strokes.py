"""StrokeData: Korean-standard stroke order/shape with a reviewed flag.

Coordinates use hanzi-writer's 1000x1000 space. Each stroke is a median polyline.
A character is *gradable* only when (a) its reviewed flag is set and (b) its stroke
data matches the Korean reference data with zero differences. Otherwise the app
shows the animation only and mock exams must not draw a writing question from it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

Point = Tuple[float, float]
DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "stroke_data"


@dataclass(frozen=True)
class StrokeData:
    char: str
    strokes: Tuple[Tuple[Point, ...], ...]  # ordered; each a median polyline
    reviewed: bool = False

    @property
    def stroke_count(self) -> int:
        return len(self.strokes)


def _parse(raw: dict, default_reviewed: bool) -> Dict[str, StrokeData]:
    out: Dict[str, StrokeData] = {}
    for ch, v in raw.items():
        strokes = tuple(tuple((float(x), float(y)) for x, y in s) for s in v["strokes"])
        out[ch] = StrokeData(ch, strokes, bool(v.get("reviewed", default_reviewed)))
    return out


def load_stroke_file(path: Path, default_reviewed: bool = False) -> Dict[str, StrokeData]:
    if not path.exists():
        return {}
    return _parse(json.loads(path.read_text(encoding="utf-8")), default_reviewed)


def load_db() -> Dict[str, StrokeData]:
    """Corrected hanzi-writer-derived data with per-character reviewed flags."""
    return load_stroke_file(DATA_DIR / "strokes.json")


def load_reference() -> Dict[str, StrokeData]:
    """Korean educational stroke-order reference (external input)."""
    return load_stroke_file(DATA_DIR / "korean_reference.json", default_reviewed=True)


def compare_stroke_data(
    db: Dict[str, StrokeData], ref: Dict[str, StrokeData], chars: Sequence[str], eps: float = 0.0
) -> List[str]:
    """Problems for every target char whose strokes differ from the reference (100% match required)."""
    problems: List[str] = []
    for ch in chars:
        d, r = db.get(ch), ref.get(ch)
        if d is None:
            problems.append(f"{ch}: missing in stroke DB")
        elif r is None:
            problems.append(f"{ch}: missing in Korean reference")
        elif d.stroke_count != r.stroke_count:
            problems.append(f"{ch}: stroke count {d.stroke_count} != reference {r.stroke_count}")
        else:
            for i, (a, b) in enumerate(zip(d.strokes, r.strokes), 1):
                if len(a) != len(b) or any(abs(p[0] - q[0]) > eps or abs(p[1] - q[1]) > eps for p, q in zip(a, b)):
                    problems.append(f"{ch}: stroke {i} differs from reference")
                    break
    return problems


def gradable_chars(db: Dict[str, StrokeData], ref: Dict[str, StrokeData]) -> List[str]:
    """Reviewed AND identical to the reference: the only chars graded or used in mock-exam writing."""
    return sorted(c for c, d in db.items() if d.reviewed and not compare_stroke_data(db, ref, [c]))


def is_gradable(char: str, db: Dict[str, StrokeData], ref: Dict[str, StrokeData]) -> bool:
    d = db.get(char)
    return bool(d and d.reviewed and not compare_stroke_data(db, ref, [char]))
