"""AC6 sample review sheet: draws >= 200 mock-exam questions exactly as the exam service builds them
(every body/grade with a confirmed format, several seeds) and writes a sheet for a HUMAN reviewer.

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
from mockexam.questions import generate_ai_questions, generate_questions, verify_question  # noqa: E402
from mockexam.service import cumulative_pool  # noqa: E402
from tracing.strokes import gradable_chars, load_db  # noqa: E402

OUT = ROOT / "reports"
CSV = OUT / "exam_sample_review.csv"
SUMMARY = OUT / "exam_sample_review_summary.json"
COLS = ["no", "body", "grade", "seed", "qid", "type", "source", "prompt", "options", "answer", "explanation",
        "db_hun", "db_eum", "auto_check", "auto_problems", "human_verdict", "human_note"]


def build(min_total: int, per_exam: int, ai_chars: int):
    store = HanjaStore.load()
    formats, assign, hanja = F.load_formats(), store.assignment_sets(), hanja_by_body(store)
    wchars = gradable_chars(load_db())
    rows, seed = [], 0
    keys = sorted(formats)
    while len(rows) < min_total:
        for body, grade in keys:
            fmt = formats[(body, grade)]
            pool = cumulative_pool(body, grade, store.bodies[body], assign, hanja[body])
            db = {h.char: h for h in pool}
            qs = generate_questions(fmt, pool, wchars if fmt.has_writing else [], random.Random(seed), db=db)
            for q in random.Random(seed + 1000).sample(qs, min(per_exam, len(qs))):
                rows.append((body, grade, seed, q, db))
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
            rows.append((body, grade, "ai", q, db))
    return rows


def write(rows) -> dict:
    OUT.mkdir(exist_ok=True)
    bad = 0
    fails = {"db": 0, "ai": 0}
    with CSV.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(COLS)
        for i, (body, grade, seed, q, db) in enumerate(rows, 1):
            probs = verify_question(q, db)
            bad += bool(probs)
            fails[q.source] += bool(probs)
            h = db.get(q.char)
            w.writerow([i, body, grade, seed, q.id, q.type, q.source, q.prompt, " | ".join(q.options), q.answer,
                        q.explanation, h.hun if h else "", h.eum if h else "", "fail" if probs else "pass",
                        "; ".join(probs), "", ""])
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "sheet": str(CSV.relative_to(ROOT)),
        "sample_size": len(rows), "by_source": {s: sum(1 for r in rows if r[3].source == s) for s in ("db", "ai")},
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
