import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hanja_db import verify as v  # noqa: E402

BODIES = {"b1": ["8", "7"]}


def test_exact_match_has_no_problems():
    off = {("b1", "8"): set("一二三"), ("b1", "7"): set("四五")}
    assert v.compare_assignments(off, {k: set(s) for k, s in off.items()}, BODIES) == []


def test_missing_and_extra_detected():
    off = {("b1", "8"): set("一二三"), ("b1", "7"): set("四五")}
    db = {("b1", "8"): set("一二"), ("b1", "7"): set("四五六")}
    probs = v.compare_assignments(off, db, BODIES)
    assert any("b1/8" in p and "missing" in p and "三" in p for p in probs)
    assert any("b1/7" in p and "extra" in p and "六" in p for p in probs)


def test_absent_official_file_and_unsupported_grade_flagged():
    probs = v.compare_assignments({("b1", "8"): set("一")}, {("b1", "8"): set("一"), ("b1", "2"): set("二")}, BODIES)
    assert any("b1/7" in p and "official list file missing" in p for p in probs)
    assert any("b1/2" in p and "not supported" in p for p in probs)


def test_entries_need_hun_eum_reviewed():
    db = {("b1", "8"): set("一二三")}
    hanja = {
        "一": {"char": "一", "hun": "한", "eum": "일", "reviewed": "1"},
        "二": {"char": "二", "hun": "", "eum": "이", "reviewed": "1"},
        "三": {"char": "三", "hun": "석", "eum": "삼", "reviewed": "0"},
    }
    probs = v.validate_entries(db, hanja)
    assert "二: missing 훈" in probs and "三: not reviewed" in probs and len(probs) == 2


def test_parse_official_list_ignores_comments_and_noise(tmp_path):
    f = tmp_path / "8.txt"
    f.write_text("# 8급\n一 한 일\n二, 三\n", encoding="utf-8")
    assert v.parse_official_list(f) == set("一二三")


def test_supported_bodies_config():
    b = v.load_exam_bodies()
    assert b["korea_eomunhoe"] == ["8", "7II", "7", "6II", "6", "5II", "5", "4II", "4"]
    assert b["daegeom"] == ["8", "7", "6", "5", "4", "3"]


def test_real_db_matches_official_public_lists():
    """AC1 on real data. Requires official list files in data/official_lists/<body>/<grade>.txt."""
    official = v.load_official_lists()
    if not official:
        pytest.skip("BLOCKED: no official public 배정한자 list files in data/official_lists/ (external input required)")
    db = v.load_db_assignments()
    problems = v.compare_assignments(official, db, v.load_exam_bodies())
    problems += v.validate_entries(db, v.load_db_hanja())
    assert problems == []
