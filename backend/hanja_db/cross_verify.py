"""Cross-check per-body hun/eum (data/hanja_db/hanja_by_body.csv) against reference sources.

Sources (local use only, see data/SOURCES.md):
  * 대한검정회: data/official_lists/daegeom/<grade>급 신출한자 훈음표*.pdf  (표제훈음, text extracted with pypdf)
  * 한국어문회: data/official_lists/korea_eomunhoe/eomunhoe-hanja.csv  (meaning = [[[훈, ...], [음, ...]], ...])

Rules
  * 대한검정회: the PDF reading run ("훈[,훈2]음", spaces ignored) must equal the DB 훈+음.
  * 한국어문회: every DB 훈 (split on "/") must be one of a CSV reading's 훈 and the DB 음 one of its 음.
  * Compatibility ideographs (e.g. 金 U+F90A) are compared after NFC normalisation.

Usage:
  python -I backend/hanja_db/cross_verify.py            # report only -> data/hanja_db/review_report.csv
  python -I backend/hanja_db/cross_verify.py --apply    # also set reviewed=1 on rows whose verdict is a match
Requires pypdf (pinned in backend/requirements.txt on the app branch; `pip install pypdf==5.1.0`).
"""
from __future__ import annotations

import argparse
import ast
import collections
import csv
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
CJK = r"[㐀-䶿一-鿿豈-﫿]"
READING = r"[가-힣\s,()\[\]/·]+"
# Reviewed by reading the source where the reference file itself is malformed (documented in SOURCES.md).
MANUAL_OK = {("korea_eomunhoe", "占"): "CSV row is malformed ('점령할점'); its readings 점령할·점칠 점 match the DB."}


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def squash(s: str | None) -> str:
    return re.sub(r"\s+", "", s or "")


def load_daegeom_pdfs(folder: Path) -> dict[str, dict[str, str]]:
    from pypdf import PdfReader

    out: dict[str, dict[str, str]] = {}
    for pdf in sorted(folder.glob("*신출한자 훈음표*.pdf")):
        grade = pdf.name.split("급")[0]
        text = "\n".join((p.extract_text() or "") for p in PdfReader(str(pdf)).pages)
        body = "".join(l for l in text.splitlines()
                       if not l.strip().startswith("※") and "표제훈음" not in l and not re.fullmatch(r"[-\d\s]*", l))
        out[grade] = {nfc(c): squash(r) for c, r in re.findall(rf"({CJK})({READING})", body)}
    return out


def load_eomunhoe_csv(path: Path) -> dict[str, list[tuple[list[str], list[str]]]]:
    out: dict[str, list] = collections.defaultdict(list)
    with path.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            for huns, eums in ast.literal_eval(r["meaning"]):
                out[nfc(r["hanja"])].append(([squash(h) for h in huns], [squash(e) for e in eums]))
    return out


def judge(row: dict, dg: dict, eo: dict) -> tuple[str, str]:
    c = nfc(row.get("char_nfc") or row["char"])
    hun, eum = squash(row["hun"]), squash(row["eum"])
    if (row["body"], c) in MANUAL_OK:
        return "match_manual", MANUAL_OK[(row["body"], c)]
    if row["body"] == "daegeom":
        src = dg.get(row["grade"], {}).get(c)
        if src is None:
            return "no_source", f"not in {row['grade']}급 PDF"
        if not hun or not eum:
            return "db_empty", f"PDF: {src}"
        heads = [squash(h) for h in re.split(r"[,/]", row["hun"]) if h.strip()]
        if hun + eum == src or any(h + eum == src for h in heads):
            return "match", ""
        # PDF writes an alternate reading in parentheses before the headword 음, e.g. 暴 "사나울(폭)포".
        alt = re.sub(r"\([가-힣]\)(?=[가-힣]$)", "", src)
        if alt != src and hun + eum == alt:
            return "match", f"PDF lists alternate reading: {src}"
        return "mismatch", f"PDF: {src}"
    cands = eo.get(c)
    if not cands:
        return "no_source", "not in CSV"
    if not hun or not eum:
        return "db_empty", ""
    parts = [squash(h) for h in row["hun"].split("/") if h.strip()]
    if any(all(p in hs for p in parts) and eum in es for hs, es in cands):
        return "match", ""
    return "mismatch", "CSV: " + " / ".join(",".join(hs) + " " + ",".join(es) for hs, es in cands)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="set reviewed=1 where the verdict is a match")
    args = ap.parse_args()
    dg = load_daegeom_pdfs(DATA / "official_lists" / "daegeom")
    eo = load_eomunhoe_csv(DATA / "official_lists" / "korea_eomunhoe" / "eomunhoe-hanja.csv")
    db_path = DATA / "hanja_db" / "hanja_by_body.csv"
    with db_path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        fields, rows = reader.fieldnames, list(reader)
    report, stats = [], collections.Counter()
    for r in rows:
        verdict, note = judge(r, dg, eo)
        stats[(r["body"], verdict)] += 1
        source = "daegeom PDF 신출한자 훈음표" if r["body"] == "daegeom" else "eomunhoe-hanja.csv"
        report.append(dict(body=r["body"], grade=r["grade"], char=r["char"], hun=r["hun"], eum=r["eum"],
                           verdict=verdict, source=source, note=note))
        if args.apply and verdict.startswith("match"):
            r["reviewed"] = "1"
    with (DATA / "hanja_db" / "review_report.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(report[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(report)
    if args.apply:
        with db_path.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
    print("PDF readings per grade:", {g: len(v) for g, v in sorted(dg.items())})
    for k in sorted(stats):
        print(*k, stats[k])
    bad = sum(v for (b, verdict), v in stats.items() if not verdict.startswith("match"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
