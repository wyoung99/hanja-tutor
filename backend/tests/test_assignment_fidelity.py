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


def test_body_entries_are_checked_per_body():
    db = {("b1", "8"): set("兄一"), ("b2", "8"): set("兄")}
    readings = {
        ("b1", "兄"): {"hun": "형", "eum": "형", "reviewed": "1"},
        ("b1", "一"): {"hun": "한", "eum": "", "reviewed": "1"},
        ("b2", "兄"): {"hun": "맏", "eum": "형", "reviewed": "0"},
    }
    probs = v.validate_body_entries(db, readings)
    assert probs == ["b1/一: missing 음", "b2/兄: not reviewed"]
    assert v.validate_body_entries({("b3", "8"): set("兄")}, readings) == ["b3/兄: no reading row"]


def test_supported_bodies_config():
    b = v.load_exam_bodies()
    assert b["korea_eomunhoe"] == ["8", "7II", "7", "6II", "6", "5II", "5", "4II", "4"]
    # 대한검정회: 준급 included, in official order (decision 2026-11-05)
    assert b["daegeom"] == ["8", "7", "6", "준5", "5", "준4", "4", "준3", "3"]


def test_real_db_matches_official_public_lists():
    """AC1 (list part, strict): DB assignments == official public lists, 0 missing / 0 extra, every grade."""
    official = v.load_official_lists()
    if not official:
        pytest.skip("BLOCKED: no official public 배정한자 list files in data/official_lists/ (external input required)")
    db = v.load_db_assignments()
    assert v.compare_assignments(official, db, v.load_exam_bodies()) == []
    # official cumulative totals for 대한검정회 (5급 250, 4급 600, 3급 1000)
    order = v.load_exam_bodies()["daegeom"]
    cum = lambda g: len(set().union(*(db[("daegeom", x)] for x in order[: order.index(g) + 1])))  # noqa: E731
    assert (cum("5"), cum("4"), cum("3")) == (250, 600, 1000)


def test_real_db_has_one_reading_row_per_assigned_body_char():
    """Structure (strict): hanja_by_body.csv is 1:1 with (body, char) assignments."""
    db = v.load_db_assignments()
    readings = v.load_db_readings()
    pairs = {(b, ch) for (b, _g), chars in db.items() for ch in chars}
    assert pairs == set(readings)
    assert not [p for p in v.validate_body_entries(db, readings) if p.endswith("no reading row")]


def test_real_db_hun_eum_reviewed():
    """AC1 (review part): every (body, char) has 훈·음 and reviewed=1.

    Human review is an external input; reviewed flags are never set by code. While review is pending this
    test skips with the exact count; any *other* kind of problem fails.
    """
    db = v.load_db_assignments()
    problems = v.validate_body_entries(db, v.load_db_readings())
    unreviewed = [p for p in problems if p.endswith(": not reviewed")]
    missing = [p for p in problems if p.endswith(("missing 훈", "missing 음"))]
    other = [p for p in problems if p not in unreviewed and p not in missing]
    assert other == []
    if unreviewed or missing:
        pytest.skip(f"PENDING human review: {len(unreviewed)} rows reviewed=0"
                    + (f"; {len(missing)} empty 훈/음 field(s) to fill in review: {', '.join(missing)}" if missing else ""))
