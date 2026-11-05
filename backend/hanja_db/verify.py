"""Compare the DB's per-grade assigned hanja with official public list files.

Official list file format (data/official_lists/<body_key>/<grade>.txt):
  UTF-8 text; every CJK ideograph in the file is one assigned character;
  '#' starts a comment line; all other characters (whitespace, hangul) are ignored.
DB files (data/hanja_db/):
  hanja_by_body.csv body,grade,char,hun,eum,reviewed,char_nfc,source_raw
                    per-(body, char) 훈·음 — the source of truth (reviewed: "1" means 검수 완료)
  assignments.csv   body,grade,char
  hanja.csv         char,hun,eum,reviewed,... (legacy, compatibility only)
"""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
HANJA_RE = re.compile(r"[一-鿿㐀-䶿豈-﫿]")


def load_exam_bodies(path: Path = DATA / "exam_bodies.json") -> dict[str, list[str]]:
    cfg = json.loads(path.read_text(encoding="utf-8"))["bodies"]
    return {k: list(v["grades"]) for k, v in cfg.items()}


def parse_official_list(path: Path) -> set[str]:
    chars: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            continue
        chars.update(HANJA_RE.findall(line))
    return chars


def load_official_lists(root: Path = DATA / "official_lists") -> dict[tuple[str, str], set[str]]:
    out: dict[tuple[str, str], set[str]] = {}
    if not root.is_dir():
        return out
    for body_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for f in sorted(body_dir.glob("*.txt")):
            out[(body_dir.name, f.stem)] = parse_official_list(f)
    return out


def load_db_assignments(path: Path = DATA / "hanja_db" / "assignments.csv") -> dict[tuple[str, str], set[str]]:
    out: dict[tuple[str, str], set[str]] = {}
    if not path.is_file():
        return out
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            out.setdefault((row["body"], row["grade"]), set()).add(row["char"])
    return out


def load_db_hanja(path: Path = DATA / "hanja_db" / "hanja.csv") -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8", newline="") as fh:
        return {row["char"]: row for row in csv.DictReader(fh)}


def load_db_readings(
    path: Path = DATA / "hanja_db" / "hanja_by_body.csv",
) -> dict[tuple[str, str], dict[str, str]]:
    """(body, raw char) -> row of the per-body 훈·음 table."""
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8", newline="") as fh:
        return {(row["body"], row["char"]): row for row in csv.DictReader(fh)}


def compare_assignments(
    official: dict[tuple[str, str], set[str]],
    db: dict[tuple[str, str], set[str]],
    bodies: dict[str, list[str]],
) -> list[str]:
    """Return problems: missing/extra chars per supported grade, absent lists, unsupported grades."""
    problems: list[str] = []
    for body, grades in bodies.items():
        for grade in grades:
            key = (body, grade)
            if key not in official:
                problems.append(f"{body}/{grade}: official list file missing")
                continue
            if key not in db:
                problems.append(f"{body}/{grade}: no DB assignments")
                continue
            missing = official[key] - db[key]
            extra = db[key] - official[key]
            if missing:
                problems.append(f"{body}/{grade}: missing in DB {''.join(sorted(missing))}")
            if extra:
                problems.append(f"{body}/{grade}: extra in DB {''.join(sorted(extra))}")
    supported = {(b, g) for b, gs in bodies.items() for g in gs}
    for key in sorted(set(db) - supported):
        problems.append(f"{key[0]}/{key[1]}: DB grade not supported by exam_bodies.json")
    return problems


def validate_entries(
    db: dict[tuple[str, str], set[str]], hanja: dict[str, dict[str, str]]
) -> list[str]:
    """Every assigned char needs 훈, 음 and reviewed == '1'."""
    problems: list[str] = []
    assigned = set().union(*db.values()) if db else set()
    for ch in sorted(assigned):
        row = hanja.get(ch)
        if row is None:
            problems.append(f"{ch}: no hanja.csv row")
            continue
        if not (row.get("hun") or "").strip():
            problems.append(f"{ch}: missing 훈")
        if not (row.get("eum") or "").strip():
            problems.append(f"{ch}: missing 음")
        if (row.get("reviewed") or "").strip() != "1":
            problems.append(f"{ch}: not reviewed")
    return problems


def validate_body_entries(
    db: dict[tuple[str, str], set[str]], readings: dict[tuple[str, str], dict[str, str]]
) -> list[str]:
    """Per (body, char): a reading row must exist, with 훈, 음 and reviewed == '1'.

    Problem strings: '<body>/<char>: no reading row' | 'missing 훈' | 'missing 음' | 'not reviewed'.
    """
    problems: list[str] = []
    pairs = sorted({(body, ch) for (body, _g), chars in db.items() for ch in chars})
    for body, ch in pairs:
        row = readings.get((body, ch))
        if row is None:
            problems.append(f"{body}/{ch}: no reading row")
            continue
        if not (row.get("hun") or "").strip():
            problems.append(f"{body}/{ch}: missing 훈")
        if not (row.get("eum") or "").strip():
            problems.append(f"{body}/{ch}: missing 음")
        if (row.get("reviewed") or "").strip() != "1":
            problems.append(f"{body}/{ch}: not reviewed")
    return problems


def main() -> int:
    problems = compare_assignments(load_official_lists(), load_db_assignments(), load_exam_bodies())
    problems += validate_body_entries(load_db_assignments(), load_db_readings())
    for p in problems:
        print(p)
    print("OK" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
