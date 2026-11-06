"""data/exam_formats.json must match the official exam format files of both bodies.

Official sources (the only place the numbers come from):
  data/official_lists/korea_eomunhoe/exam_formats_official.json  (hanja.re.kr questInfo/successRule/examTime)
  data/official_lists/daegeom/exam_formats_official.json         (대한검정회 안내 표, 사용자 제공)
"""
import json
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from mockexam import formats as F  # noqa: E402
from mockexam import make_formats  # noqa: E402

DATA = ROOT / "data"
CFG = json.loads((DATA / "exam_formats.json").read_text(encoding="utf-8"))
EO = json.loads((DATA / "official_lists/korea_eomunhoe/exam_formats_official.json").read_text(encoding="utf-8"))
DG = json.loads((DATA / "official_lists/daegeom/exam_formats_official.json").read_text(encoding="utf-8"))
BUILT = {"hunum": "hun_eum", "hanja_sseugi": "writing", "pilsun": "pilsun", "busu": "busu"}
FORMATS = F.load_formats()


def test_config_is_generated_from_the_official_files():
    assert CFG == json.loads(json.dumps(make_formats.build(), ensure_ascii=False))
    assert CFG["format_pending"] == {} and F.load_pending() == {}
    s = CFG["sources"]
    assert s["korea_eomunhoe"]["types"] == EO["sources"]["types"] and s["korea_eomunhoe"]["pass"] == EO["sources"]["pass"]
    assert s["korea_eomunhoe"]["minutes"] == EO["sources"]["minutes"]
    assert s["daegeom"]["reference"] == DG["source"]


@pytest.mark.parametrize("grade", list(EO["grades"]))
def test_eomunhoe_matches_official_totals_pass_minutes_and_type_counts(grade):
    off, cfg = EO["grades"][grade], CFG["bodies"]["korea_eomunhoe"][grade]
    assert (cfg["total"], cfg["pass_questions"], cfg["pass_score"], cfg["minutes"]) == (
        off["total"], off["pass"], off["pass"], off["minutes"])
    assert {t["official"]: t["count"] for t in cfg["types"]} == off["types"]  # every official type, official count
    assert all(t["label"] == EO["type_labels"][t["official"]] and t["points"] == 1 for t in cfg["types"])
    built = sum(t["count"] for t in cfg["types"] if t["official"] in BUILT)
    subs = cfg["substitutions"]
    assert built + sum(v["count"] for v in subs.values()) == off["total"]  # substitutions fill up to the total
    for t in cfg["types"]:
        if t["official"] in BUILT:
            assert t["type"] == BUILT[t["official"]] and not t.get("substituted")
        else:
            assert t["substituted"] and subs[t["official"]]["count"] == t["count"] == off["types"][t["official"]]
            assert subs[t["official"]]["with"] == t["type"] and subs[t["official"]]["reason"]
    assert set(subs) == set(off["types"]) - set(BUILT)
    assert cfg["type_mix"] == ("partial_substitution" if subs else "official")
    assert cfg["notice"] == (F.SUBSTITUTION_NOTICE if subs else None)
    v = cfg["verified"]
    assert v["total"] and v["pass"] and v["minutes"] and v["type_counts"] and v["question_kinds"] is (not subs)
    f = FORMATS[("korea_eomunhoe", grade)]
    assert (f.question_count, f.pass_score, f.minutes, f.total_score) == (off["total"], off["pass"], 50, off["total"])


def test_eomunhoe_new_types_are_built_where_official():
    for grade, off in EO["grades"].items():
        f = FORMATS[("korea_eomunhoe", grade)]
        for key in ("hunum", "hanja_sseugi", "pilsun", "busu"):
            got = [t for t in f.types if t.key == key]
            assert [t.count for t in got] == ([off["types"][key]] if key in off["types"] else [])
    # 讀音 -> eum is the documented closest substitute in every grade
    assert all(CFG["bodies"]["korea_eomunhoe"][g]["substitutions"]["dokeum"]["with"] == "eum" for g in EO["grades"])


@pytest.mark.parametrize("grade", list(DG["grades"]))
def test_daegeom_matches_official_totals_pass_points_minutes_all_multiple_choice(grade):
    off, cfg = DG["grades"][grade], CFG["bodies"]["daegeom"][grade]
    assert (cfg["total"], cfg["pass_questions"], cfg["pass_score"], cfg["points_per_question"], cfg["minutes"]) == (
        off["total"], off["pass_questions"], off["pass_points"], off["points_per_question"], off["minutes"])
    assert sum(t["count"] for t in cfg["types"]) == off["total"]
    assert {t["type"] for t in cfg["types"]} <= {"eum", "hun_eum", "hun_to_hanja"}  # no writing at all
    assert all(t["points"] == off["points_per_question"] for t in cfg["types"])
    assert cfg["type_mix"] == "app_defined" and cfg["substitutions"] == {}
    v = cfg["verified"]
    assert v["total"] and v["pass"] and v["minutes"] and v["points"] and v["all_multiple_choice"]
    assert v["type_counts"] is False  # the per-type mix is app-defined, never marked official
    f = FORMATS[("daegeom", grade)]
    assert not f.has_writing and f.all_choice and f.total_score == 100
    assert off["pass_questions"] * off["points_per_question"] >= off["pass_points"] > (
        off["pass_questions"] - 1) * off["points_per_question"]


def test_every_supported_grade_of_both_bodies_has_an_official_format():
    bodies = json.loads((DATA / "exam_bodies.json").read_text(encoding="utf-8"))["bodies"]
    assert list(CFG["bodies"]["korea_eomunhoe"]) == list(EO["grades"]) == bodies["korea_eomunhoe"]["grades"]
    assert list(CFG["bodies"]["daegeom"]) == list(DG["grades"]) == bodies["daegeom"]["grades"]


def test_every_format_generates_from_the_real_db():
    """部首/筆順/漢字 쓰기 really have enough real data (radicals, matching stroke counts) in every grade."""
    from app.main import hanja_by_body
    from hanja_db.store import HanjaStore
    from mockexam.questions import generate_questions, verify_question
    from mockexam.service import cumulative_pool
    from tracing.strokes import gradable_chars, load_db

    store = HanjaStore.load()
    hanja, assign, sdb = hanja_by_body(store), store.assignment_sets(), load_db()
    wchars = gradable_chars(sdb)
    for (body, grade), fmt in FORMATS.items():
        pool = cumulative_pool(body, grade, store.bodies[body], assign, hanja[body])
        db = {h.char: h for h in pool}
        for seed in range(3):
            qs = generate_questions(fmt, pool, wchars, random.Random(seed), db=db, strokes=sdb)
            assert len(qs) == fmt.question_count
            assert all(not verify_question(q, db, sdb) for q in qs)
            for q in qs:
                if q.type == "busu":
                    assert q.answer == db[q.char].radical
                if q.type == "pilsun":
                    assert sdb[q.char].stroke_count == db[q.char].total_strokes
