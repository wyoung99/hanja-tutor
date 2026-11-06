"""Generate data/stroke_data/strokes.json from a hanzi-writer-data npm tarball.

Usage (run isolated; the tarball is untrusted *data* and is never extracted or executed):

    npm pack hanzi-writer-data@2.0.1          # into an empty temp dir
    python -I backend/tracing/build_stroke_data.py <path/to/hanzi-writer-data-2.0.1.tgz>

Only ``package/<char>.json`` members are read, and only their ``medians`` arrays (numbers) are kept.
Characters come from data/hanja_db/assignments.csv, NFC-normalised (the source lists use CJK
compatibility code points such as 金 U+F90A; hanzi-writer files are keyed by the unified char).

Coordinates: Make Me a Hanzi / hanzi-writer use a 1024-unit box with the y axis pointing up and the
glyph shifted so that screen_y = 900 - y. We convert to the app's 0..1000 screen space (y down):
    x' = x * 1000 / 1024,   y' = (900 - y) * 1000 / 1024
Self-contained on purpose (no repo imports) so it can run with ``python -I``.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
import tarfile
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSIGNMENTS = ROOT / "data" / "hanja_db" / "assignments.csv"
OUT = ROOT / "data" / "stroke_data" / "strokes.json"
PACKAGE = "hanzi-writer-data"
SCALE = 1000 / 1024


def _convert(medians) -> list:
    out = []
    for stroke in medians:
        pts = []
        for x, y in stroke:
            x, y = float(x), float(y)  # numbers only; anything else raises
            pts.append([round(x * SCALE), round((900 - y) * SCALE)])
        if len(pts) < 2:
            raise ValueError("median with fewer than 2 points")
        out.append(pts)
    if not out:
        raise ValueError("no strokes")
    return out


def assigned_chars() -> list[str]:
    seen: dict[str, None] = {}
    with ASSIGNMENTS.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            seen.setdefault(unicodedata.normalize("NFC", row["char"]), None)
    return list(seen)


def build(tgz: Path) -> tuple[dict, list[str]]:
    sha = hashlib.sha256(tgz.read_bytes()).hexdigest()
    chars = assigned_chars()
    data: dict[str, dict] = {}
    missing: list[str] = []
    with tarfile.open(tgz, "r:gz") as tf:
        meta = json.loads(tf.extractfile("package/package.json").read().decode("utf-8"))
        if meta.get("name") != PACKAGE:
            raise SystemExit(f"unexpected package {meta.get('name')!r}")
        names = set(tf.getnames())
        for ch in chars:
            member = f"package/{ch}.json"
            if member not in names:
                missing.append(ch)
                continue
            raw = json.loads(tf.extractfile(member).read().decode("utf-8"))
            data[ch] = {"strokes": _convert(raw["medians"]), "exclude_from_grading": False}
    doc = {
        "_source": {
            "package": PACKAGE,
            "version": meta.get("version"),
            "tarball_sha256": sha,
            "upstream": "Make Me a Hanzi (https://github.com/skishore/makemeahanzi), Arphic fonts",
            "license": "Arphic Public License (see data/SOURCES.md); local use only",
            "coordinates": "0..1000 screen space, y down; stroke = median polyline (x*1000/1024, (900-y)*1000/1024)",
            "korean_standard_verified": False,
            "note": "Korean-standard stroke-order verification excluded by user decision 2026-11-05 (AC7). "
                    "Set exclude_from_grading=true on a char to drop it from grading/writing questions.",
            "missing": "".join(missing),
        },
        "chars": data,
    }
    return doc, missing


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    doc, missing = build(Path(argv[1]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"wrote {len(doc['chars'])} chars to {OUT}")
    print(f"missing ({len(missing)}): {''.join(missing)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
