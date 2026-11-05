import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tracing import strokes as S  # noqa: E402
from tracing import grader as G  # noqa: E402
from study.planner import StudyPlanner, PlanHanja, REASON_TRACING_ANSWER_SEEN  # noqa: E402

KST = timezone(timedelta(hours=9))
# 二-like: stroke 1 horizontal short (top), stroke 2 horizontal long (bottom); 川-like not needed
TOP = ((200, 300), (500, 300), (800, 300))
BOT = ((100, 700), (500, 700), (900, 700))
VERT = ((500, 100), (500, 500), (500, 900))
DATA = S.StrokeData("二", (TOP, BOT), reviewed=True)
DATA3 = S.StrokeData("川", (TOP, BOT, VERT), reviewed=True)


def test_correct_strokes_pass():
    a = G.TracingAttempt(DATA)
    assert a.submit(TOP).ok and a.submit(BOT).ok
    assert a.result == "pass"


def test_direction_strict_reversed_rejected_and_highlighted():
    a = G.TracingAttempt(DATA)
    fb = a.submit(TOP[::-1])
    assert not fb.ok and fb.error == G.ERR_DIRECTION and fb.highlight_stroke
    assert a.current == 0 and a.submit(TOP).ok  # redo same stroke


def test_order_strict():
    a = G.TracingAttempt(DATA)
    fb = a.submit(BOT)  # second stroke first
    assert not fb.ok and fb.error == G.ERR_ORDER


def test_position_within_tolerance_ok_outside_fails():
    near = tuple((x, y + 100) for x, y in TOP)
    far = tuple((x, y - 400) for x, y in TOP)
    assert G.grade_stroke(near, DATA, 0) is None
    assert G.grade_stroke(far, DATA, 0) == G.ERR_POSITION
    # tolerance is a setting
    assert G.grade_stroke(near, DATA, 0, G.GradeTolerance(position=50)) == G.ERR_POSITION


def test_length_tolerance():
    short = ((200, 300), (260, 300))
    long_ = ((-200, 300), (1200, 300))
    assert G.grade_stroke(short, DATA, 0) == G.ERR_LENGTH
    assert G.grade_stroke(long_, DATA, 0) == G.ERR_LENGTH
    assert G.grade_stroke(long_, DATA, 0, G.GradeTolerance(length_max_ratio=3.0)) is None


def test_three_errors_same_stroke_reveals_answer_and_result_is_retry():
    a = G.TracingAttempt(DATA)
    bad = TOP[::-1]
    assert not a.submit(bad).reveal_answer
    assert not a.submit(bad).reveal_answer
    assert a.submit(bad).reveal_answer
    a.submit(TOP)
    a.submit(BOT)
    assert a.result == "retry"


def test_errors_on_different_strokes_do_not_reveal():
    a = G.TracingAttempt(DATA)
    a.submit(TOP[::-1]); a.submit(TOP[::-1]); a.submit(TOP)
    fb = None
    for _ in range(2):
        fb = a.submit(BOT[::-1])
    assert not fb.reveal_answer
    a.submit(BOT)
    assert a.result == "pass"  # eventually all strokes right, answer never shown


def test_result_only_pass_or_retry_and_none_while_unfinished():
    a = G.TracingAttempt(DATA)
    assert a.result is None
    a.submit(TOP)
    assert a.result is None


def test_two_different_days_completes_writing():
    p = G.TracingProgress()
    day1 = datetime(2026, 3, 2, 14, 59, tzinfo=timezone.utc)  # 23:59 KST Mar 2
    day1b = datetime(2026, 3, 2, 15, 1, tzinfo=timezone.utc)  # 00:01 KST Mar 3 -> next KST day
    a = G.TracingAttempt(DATA); a.submit(TOP); a.submit(BOT)
    p.record("二", a, lambda: day1)
    assert not p.completed("二")
    b = G.TracingAttempt(DATA); b.submit(TOP); b.submit(BOT)
    p.record("二", b, lambda: day1)  # same day again
    assert not p.completed("二")
    c = G.TracingAttempt(DATA); c.submit(TOP); c.submit(BOT)
    p.record("二", c, lambda: day1b)
    assert p.completed("二") and p.completed_count() == 1


def test_retry_does_not_count_and_feeds_review():
    clock = lambda: datetime(2026, 3, 2, 3, 0, tzinfo=KST)  # noqa: E731
    planner = StudyPlanner([PlanHanja("二", 1)], clock=clock)
    p = G.TracingProgress()
    a = G.TracingAttempt(DATA)
    for _ in range(3):
        a.submit(TOP[::-1])
    a.submit(TOP); a.submit(BOT)
    res = p.record("二", a, clock, on_answer_seen=lambda c: planner.add_review(c, REASON_TRACING_ANSWER_SEEN))
    assert res == "retry" and "二" not in p.passed_days
    assert planner.reviews["二"].reason == REASON_TRACING_ANSWER_SEEN


def test_unreviewed_char_is_animation_only():
    ref = {"二": S.StrokeData("二", (TOP, BOT), True)}
    db = {"二": S.StrokeData("二", (TOP, BOT), reviewed=False)}
    out = G.start_attempt("二", db, ref)
    assert out["mode"] == "animation_only" and len(out["strokes"]) == 2
    assert S.gradable_chars(db, ref) == []
    db["二"] = S.StrokeData("二", (TOP, BOT), reviewed=True)
    assert G.start_attempt("二", db, ref)["mode"] == "graded"
    assert S.gradable_chars(db, ref) == ["二"]


def test_reviewed_but_mismatching_reference_is_not_gradable():
    ref = {"二": S.StrokeData("二", (TOP, BOT), True)}
    swapped = {"二": S.StrokeData("二", (BOT, TOP), True)}  # wrong order
    assert S.compare_stroke_data(swapped, ref, ["二"]) == ["二: stroke 1 differs from reference"]
    assert G.start_attempt("二", swapped, ref)["mode"] == "animation_only"
    fewer = {"二": S.StrokeData("二", (TOP,), True)}
    assert "stroke count" in S.compare_stroke_data(fewer, ref, ["二"])[0]
    assert "missing in Korean reference" in S.compare_stroke_data(ref, {}, ["二"])[0]
    assert S.compare_stroke_data(ref, ref, ["二"]) == []


def test_real_graded_chars_match_korean_reference_100_percent():
    """Every graded char in the real DB must equal the Korean reference. Needs data/stroke_data/*.json."""
    db, ref = S.load_db(), S.load_reference()
    if not db or not ref:
        pytest.skip("BLOCKED: data/stroke_data/{strokes,korean_reference}.json (external input) absent")
    graded = [c for c, d in db.items() if d.reviewed]
    assert S.compare_stroke_data(db, ref, graded) == []
