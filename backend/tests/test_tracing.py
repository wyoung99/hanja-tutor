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
DATA = S.StrokeData("二", (TOP, BOT))
DATA3 = S.StrokeData("川", (TOP, BOT, VERT))


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


def test_gating_stroke_data_and_exclusion_flag():
    db = {"二": S.StrokeData("二", (TOP, BOT))}
    assert G.start_attempt("二", db)["mode"] == "graded" and S.gradable_chars(db) == ["二"]
    # per-char "exclude from grading" slot: animation only, never graded / used for writing
    db["二"] = S.StrokeData("二", (TOP, BOT), exclude_from_grading=True)
    out = G.start_attempt("二", db)
    assert out["mode"] == "animation_only" and len(out["strokes"]) == 2
    assert S.gradable_chars(db) == []
    # no stroke data -> not a tracing target at all
    assert G.start_attempt("三", db)["mode"] == "unavailable"


def test_korean_standard_stroke_order_exemption_is_documented():
    """AC7: Korean-standard verification is excluded (verify_exemption_reason). Assert it is *documented*,
    then skip: no Korean reference is fabricated or compared against."""
    root = Path(__file__).resolve().parents[2]
    seed = (root / "hanja-tutor.seed.yaml").read_text(encoding="utf-8")
    sources = (root / "data" / "SOURCES.md").read_text(encoding="utf-8")
    assert "verify_exemption_reason" in seed and "2026-11-05" in seed and "한국 기준 획순" in seed
    assert "hanzi-writer-data" in sources and "검증에서 제외" in sources
    meta = S.load_source()
    if meta:
        assert meta["korean_standard_verified"] is False
    assert not (root / "data" / "stroke_data" / "korean_reference.json").exists()
    pytest.skip(S.KOREAN_STANDARD_EXEMPTION)


# ---- real hanzi-writer data (data/stroke_data/strokes.json) -------------------------------------------
REAL = S.load_db()
needs_real = pytest.mark.skipif(not REAL, reason="BLOCKED: data/stroke_data/strokes.json absent")


@needs_real
def test_real_data_covers_assigned_chars_except_documented_missing():
    from hanja_db.store import HanjaStore

    store = HanjaStore.load()
    meta = S.load_source()
    assigned = set(store.all_chars())
    missing = assigned - set(REAL)
    assert missing == set(meta["missing"])
    assert set(REAL) <= assigned and meta["package"] == "hanzi-writer-data" and meta["version"]
    for d in REAL.values():
        assert d.stroke_count >= 1 and all(len(s) >= 2 for s in d.strokes)
        assert all(0 <= x <= 1000 and -100 <= y <= 1000 for s in d.strokes for x, y in s)


@needs_real
@pytest.mark.parametrize("ch,count", [("學", 16), ("一", 1), ("兄", 5), ("金", 8), ("水", 4)])
def test_grader_accepts_real_medians_in_order(ch, count):
    data = REAL[ch]
    assert data.stroke_count == count
    a = G.TracingAttempt(data)
    for s in data.strokes:
        fb = a.submit(s)
        assert fb.ok, (ch, fb.error)
    assert a.result == "pass"


@needs_real
def test_grader_rejects_reversed_and_out_of_order_real_strokes():
    data = REAL["學"]
    # a long stroke drawn backwards is a direction error
    i = max(range(data.stroke_count), key=lambda k: G._length(data.strokes[k]))
    assert G.grade_stroke(tuple(reversed(data.strokes[i])), data, i) == G.ERR_DIRECTION
    # 十: drawing the vertical (2nd) stroke first is rejected; the right one passes
    ten = REAL["十"]
    assert G.grade_stroke(ten.strokes[1], ten, 0) is not None
    assert G.grade_stroke(ten.strokes[0], ten, 0) is None
    # three errors on the same real stroke reveal the answer and the attempt is a retry
    a = G.TracingAttempt(ten)
    for _ in range(3):
        fb = a.submit(tuple(reversed(ten.strokes[0])))
    assert fb.reveal_answer
    for s in ten.strokes:
        a.submit(s)
    assert a.result == "retry"


@needs_real
def test_nfc_lookup_for_compatibility_code_points():
    # 金 appears in the official 어문회 list as U+F90A; stroke data is keyed by the unified U+91D1
    assert G.start_attempt("金", REAL)["mode"] == "graded"
