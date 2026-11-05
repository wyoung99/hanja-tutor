"""Regenerates data/exam_formats.json (kept as a script so the table stays reviewable)."""
import json
from pathlib import Path


def g(minutes, pass_, types):
    return {"minutes": minutes, "pass_score": pass_,
            "types": [{"type": t, "count": c, "points": 1} for t, c in types if c],
            "verified": False}


def eh(n, h2hj=0, w=0):
    rest = n - h2hj - w
    a = rest // 2
    return [("eum", a), ("hun_eum", rest - a), ("hun_to_hanja", h2hj), ("writing", w)]


KOREA = {"8": g(50, 35, eh(50)), "7II": g(50, 42, eh(60)), "7": g(50, 49, eh(70)),
         "6II": g(50, 56, eh(80, 20)), "6": g(50, 63, eh(90, 24)),
         "5II": g(50, 70, eh(100, 20, 10)), "5": g(50, 70, eh(100, 20, 20)),
         "4II": g(50, 70, eh(100, 10, 30)), "4": g(50, 70, eh(100, 10, 40))}
DAEGEOM = {"8": g(40, 35, eh(50)), "7": g(40, 42, eh(60)), "6": g(40, 49, eh(70, 10)),
           "5": g(40, 56, eh(80, 16, 15)), "4": g(40, 63, eh(90, 10, 25)), "3": g(40, 70, eh(100, 10, 30))}

# Supported grades whose official exam format has NOT been obtained. They get learning features only;
# starting a mock exam is blocked with "형식 준비 중". Never invent a format for these: add one here only
# after confirming the body's published notice, and remove the grade from FORMAT_PENDING at the same time.
PENDING_REASON = "공식 출제 형식(문항 수·유형·시간·합격선)을 확보하지 못함 — 형식 준비 중"
FORMAT_PENDING = {"daegeom": {g: PENDING_REASON for g in ("준5", "준4", "준3")}}

OUT = {"_comment": "Per body/grade exam format; independent config, grades are NOT mapped across bodies. "
                   "verified=false until an operator confirms each grade against the body's published 출제기준. "
                   "pass_score is in points (1 point per question). format_pending lists supported grades "
                   "with no known official format (mock exam blocked, learning features only).",
       "bodies": {"korea_eomunhoe": KOREA, "daegeom": DAEGEOM},
       "format_pending": FORMAT_PENDING}

if __name__ == "__main__":
    p = Path(__file__).resolve().parents[2] / "data" / "exam_formats.json"
    p.write_text(json.dumps(OUT, ensure_ascii=False, indent=1), encoding="utf-8")
