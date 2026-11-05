"""AC6 sample review sheet: draws >= 200 mock-exam questions exactly as the exam service builds them
(every body/grade with a confirmed format, several seeds) and writes a sheet for a HUMAN reviewer.
Each exam sample contains every question type of that exam (incl. 筆順/部首/漢字 쓰기 for 한국어문회) before
random fill, so both bodies and all types are covered.

    python backend/scripts/exam_sample_review.py                 # 200+ DB-built questions
    python backend/scripts/exam_sample_review.py --ai-chars 10   # + Sonnet-generated items (needs AWS keys)

Outputs (reports/):
  exam_sample_review.csv           one row per question; columns human_verdict / human_note are EMPTY and
                                   must be filled by the reviewer (ok / wrong_answer / wrong_option / other)
  exam_sample_review_summary.json  automated DB cross-check counts; "human_reviewed": false until a person
                                   fills the sheet and runs --tally

    python backend/scripts/exam_sample_review.py --tally          # error rate from the filled sheet

The automated check is the same DB gate the exam uses; it is NOT the human review the criterion asks for.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.main import hanja_by_body  # noqa: E402
from hanja_db.store import HanjaStore  # noqa: E402
from mockexam import formats as F  # noqa: E402
from mockexam.questions import PILSUN_DATA_CAVEAT, generate_ai_questions, generate_questions, verify_question  # noqa: E402
from mockexam.service import cumulative_pool  # noqa: E402
from tracing.strokes import gradable_chars, load_db  # noqa: E402

OUT = ROOT / "reports"
CSV = OUT / "exam_sample_review.csv"
SUMMARY = OUT / "exam_sample_review_summary.json"
COLS = ["no", "body", "grade", "seed", "qid", "official_type", "type", "substituted", "source", "prompt", "options",
        "answer", "explanation", "db_hun", "db_eum", "db_radical", "db_total_strokes", "stroke_index",
        "data_note", "auto_check", "auto_problems", "human_verdict", "human_note"]


def stratified(qs, n, rng):
    """One question of every slot first (rarest types are covered), then random fill up to n."""
    first = {}
    for q in rng.sample(qs, len(qs)):
        first.setdefault(q.slot or q.type, q)
    picked = list(first.values())[:n]
    rest = [q for q in qs if q not in picked]
    return picked + rng.sample(rest, max(0, min(n - len(picked), len(rest))))


def build(min_total: int, per_exam: int, ai_chars: int):
    store = HanjaStore.load()
    formats, assign, hanja = F.load_formats(), store.assignment_sets(), hanja_by_body(store)
    sdb = load_db()
    wchars = gradable_chars(sdb)
    rows, seed = [], 0
    keys = sorted(formats)
    while len(rows) < min_total:
        for body, grade in keys:
            fmt = formats[(body, grade)]
            pool = cumulative_pool(body, grade, store.bodies[body], assign, hanja[body])
            db = {h.char: h for h in pool}
            qs = generate_questions(fmt, pool, wchars if fmt.has_writing else [], random.Random(seed), db=db,
                                    strokes=sdb)
            subst = {t.key for t in fmt.types if t.substituted}
            for q in stratified(qs, per_exam, random.Random(seed + 1000)):
                rows.append((body, grade, seed, q, db, sdb, q.slot in subst))
        seed += 1
    if ai_chars:
        from app.settings import apply_dotenv, aws_region
        from llm import build_llm_router

        apply_dotenv()
        llm = build_llm_router({"provider": "bedrock", "region": aws_region(os.environ)}).for_task("question_generation")
        body, grade = "korea_eomunhoe", "8"
        pool = cumulative_pool(body, grade, store.bodies[body], assign, hanja[body])
        db = {h.char: h for h in pool}
        chars = random.Random(7).sample(sorted(db), min(ai_chars, len(db)))
        kept, dropped = generate_ai_questions(llm, chars, db)
        for q in kept + [d[0] for d in dropped]:
            rows.append((body, grade, "ai", q, db, sdb, False))
    return rows


def write(rows) -> dict:
    OUT.mkdir(exist_ok=True)
    bad = 0
    fails = {"db": 0, "ai": 0}
    with CSV.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(COLS)
        for i, (body, grade, seed, q, db, sdb, subst) in enumerate(rows, 1):
            probs = verify_question(q, db, sdb)
            bad += bool(probs)
            fails[q.source] += bool(probs)
            h = db.get(q.char)
            w.writerow([i, body, grade, seed, q.id, q.slot or q.type, q.type, "yes" if subst else "", q.source,
                        q.prompt, " | ".join(q.options), q.answer, q.explanation, h.hun if h else "",
                        h.eum if h else "", h.radical if h else "", (h.total_strokes or "") if h else "",
                        q.stroke_index if q.type == "pilsun" else "",
                        PILSUN_DATA_CAVEAT if q.type == "pilsun" else "", "fail" if probs else "pass",
                        "; ".join(probs), "", ""])
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "sheet": str(CSV.relative_to(ROOT)),
        "sample_size": len(rows), "by_source": {s: sum(1 for r in rows if r[3].source == s) for s in ("db", "ai")},
        "by_body": {b: sum(1 for r in rows if r[0] == b) for b in sorted({r[0] for r in rows})},
        "by_type": {t: sum(1 for r in rows if r[3].type == t) for t in F.TYPES},
        "by_official_type": {k: sum(1 for r in rows if (r[3].slot or r[3].type) == k)
                             for k in sorted({r[3].slot or r[3].type for r in rows})},
        "substituted_items": sum(1 for r in rows if r[6]),
        "automated_db_check": {"failed": bad, "rate": bad / len(rows) if rows else None, "failed_by_source": fails,
                               "served_items_failed": fails["db"],
                               "ai_note": "AI items that fail the DB gate are discarded before an exam is served"},
        "human_reviewed": False, "human_error_rate": None, "threshold": 0.01,
        "note": "Automated check only. The AC6 criterion needs a person to fill human_verdict for every row.",
    }
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def tally() -> dict:
    with CSV.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    filled = [r for r in rows if r["human_verdict"].strip()]
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    if len(filled) < len(rows):
        summary.update(human_reviewed=False, human_error_rate=None,
                       note=f"{len(rows) - len(filled)} of {len(rows)} rows still lack human_verdict")
    else:
        wrong = sum(1 for r in filled if r["human_verdict"].strip().lower() != "ok")
        summary.update(human_reviewed=True, human_error_rate=wrong / len(rows), human_errors=wrong,
                       human_pass=wrong / len(rows) < summary["threshold"])
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=int, default=200)
    ap.add_argument("--per-exam", type=int, default=12)
    ap.add_argument("--ai-chars", type=int, default=0)
    ap.add_argument("--tally", action="store_true")
    a = ap.parse_args()
    s = tally() if a.tally else write(build(a.min, a.per_exam, a.ai_chars))
    print(json.dumps({k: v for k, v in s.items() if k != "note"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
