import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from study.planner import (  # noqa: E402
    KST,
    PlanError,
    PlanHanja,
    StudyPlanner,
    daily_quota,
)


class Clock:
    def __init__(self, dt):
        self.dt = dt

    def __call__(self):
        return self.dt


def hanja(n):
    # rank deliberately in reverse of code point order to prove frequency ordering
    return [PlanHanja(chr(0x4E00 + i), n - i) for i in range(n)]


def planner(n=90, exam=None, now=datetime(2026, 3, 1, 9, 0, tzinfo=KST)):
    clock = Clock(now)
    return StudyPlanner(hanja(n), clock=clock, exam_date=exam), clock


def test_quota_even_split_and_clamps():
    assert daily_quota(90, 30) == 3
    assert daily_quota(100, 10) == 10
    assert daily_quota(10, 100) == 3  # lower clamp
    assert daily_quota(1000, 10) == 15  # upper clamp
    assert daily_quota(2, 50) == 2  # fewer than 3 left
    assert daily_quota(0, 5) == 0


def test_even_distribution_to_exam_date_and_frequency_order():
    p, _ = planner(60, exam=date(2026, 3, 10))  # 10 days incl. today
    plan = p.plan_for_today()
    assert plan.quota == 6 and plan.days_left == 10
    ranks = [p.assigned[c].freq_rank for c in plan.new_chars]
    assert ranks == [1, 2, 3, 4, 5, 6]  # most frequent first


def test_default_90_day_distribution_without_exam_date():
    p, _ = planner(270)
    plan = p.plan_for_today()
    assert plan.days_left == 90 and plan.quota == 3
    sched = p.projected_schedule()
    assert sum(sched.values()) == 270 and len(sched) == 90


def test_quota_always_within_3_to_15_in_projection():
    p, _ = planner(200, exam=date(2026, 3, 30))
    for n in list(p.projected_schedule().values())[:-1]:
        assert 3 <= n <= 15


def test_parent_override_reflected_and_validated():
    p, _ = planner(90, exam=date(2026, 6, 1))
    p.set_daily_override(8)
    plan = p.plan_for_today()
    assert plan.quota == 8 and plan.overridden and len(plan.new_chars) == 8
    for bad in (2, 16, 0, True, "5"):
        with pytest.raises(PlanError):
            p.set_daily_override(bad)
    p.set_daily_override(None)
    assert not p.plan_for_today().overridden


def test_parent_date_override_applies_to_that_day_only():
    p, clock = planner(90, exam=date(2026, 6, 1))
    tomorrow = date(2026, 3, 2)
    p.set_date_override(tomorrow, 12)
    assert p.plan_for_today().overridden is False
    clock.dt = datetime(2026, 3, 2, 0, 0, tzinfo=KST)
    plan = p.plan_for_today()
    assert plan.quota == 12 and plan.overridden
    with pytest.raises(PlanError):
        p.set_date_override(date(2026, 3, 1), 5)  # past


def test_kst_midnight_switches_to_next_day():
    p, clock = planner(60, exam=date(2026, 3, 10), now=datetime(2026, 3, 1, 23, 59, 59, tzinfo=KST))
    d1 = p.plan_for_today()
    for c in d1.new_chars:
        p.complete_new(c)
    clock.dt = datetime(2026, 3, 2, 0, 0, 0, tzinfo=KST)
    d2 = p.plan_for_today()
    assert d2.day == date(2026, 3, 2)
    assert d2.new_chars and not set(d2.new_chars) & set(d1.new_chars)
    assert d2.days_left == 9


def test_kst_boundary_uses_kst_not_utc():
    # 15:00 UTC on Mar 1 == 00:00 KST Mar 2
    utc = timezone.utc
    p, clock = planner(60, exam=date(2026, 3, 10), now=datetime(2026, 3, 1, 14, 59, tzinfo=utc))
    assert p.plan_for_today().day == date(2026, 3, 2) - timedelta(days=1)
    clock.dt = datetime(2026, 3, 1, 15, 0, tzinfo=utc)
    assert p.plan_for_today().day == date(2026, 3, 2)


def test_plan_stable_within_day():
    p, _ = planner(60, exam=date(2026, 3, 10))
    first = p.plan_for_today()
    p.complete_new(first.new_chars[0])
    assert p.plan_for_today().new_chars == first.new_chars


def test_skipped_day_backlog_reviews_continue_new_is_one_day_and_plan_recomputed():
    p, clock = planner(60, exam=date(2026, 3, 10))
    d1 = p.plan_for_today()
    for c in d1.new_chars:
        p.complete_new(c)  # reviews due Mar 2
    # child skips Mar 2 and Mar 3, returns Mar 4
    clock.dt = datetime(2026, 3, 4, 8, 0, tzinfo=KST)
    session = p.todays_session()
    assert set(session["reviews"]) == set(d1.new_chars)  # overdue review still there
    plan = session["plan"]
    assert len(plan.new_chars) == plan.quota  # a single day's worth only
    assert plan.days_left == 7
    assert plan.quota == 8  # ceil(54 / 7): recomputed, not the original 6
    # new hanja still never repeat learned ones
    assert not set(plan.new_chars) & p.learned


def test_missed_new_hanja_return_in_recomputed_plan():
    p, clock = planner(60, exam=date(2026, 3, 10))
    d1 = p.plan_for_today()  # not completed at all
    clock.dt = datetime(2026, 3, 2, 0, 0, tzinfo=KST)
    d2 = p.plan_for_today()
    assert d2.quota == 7  # ceil(60/9)
    assert d2.new_chars[: len(d1.new_chars)] == d1.new_chars


def _at(clock, d, h=10):
    clock.dt = datetime(d.year, d.month, d.day, h, 0, tzinfo=KST)


def test_review_intervals_1_3_7_then_mastered_after_three_distinct_days():
    p, clock = planner(10, exam=date(2026, 3, 10))
    c = p.plan_for_today().new_chars[0]
    p.complete_new(c)
    for gap in (1, 3, 7):
        item = p.reviews[c]
        assert item.due == clock.dt.date() + timedelta(days=gap)
        _at(clock, item.due)
        assert c in [r.char for r in p.due_reviews()]
        p.answer_review(c, True)
    assert p.reviews[c].mastered and p.reviews[c].streak == 3
    assert c not in [r.char for r in p.due_reviews()]


def test_same_day_repeat_correct_does_not_count():
    p, clock = planner(10, exam=date(2026, 3, 10))
    c = p.plan_for_today().new_chars[0]
    p.complete_new(c)
    _at(clock, date(2026, 3, 2))
    p.answer_review(c, True)
    p.answer_review(c, True)
    assert p.reviews[c].streak == 1 and not p.reviews[c].mastered


def test_wrong_answer_steps_back_one_stage():
    p, clock = planner(10, exam=date(2026, 3, 10))
    c = p.plan_for_today().new_chars[0]
    p.complete_new(c)
    _at(clock, date(2026, 3, 2))
    p.answer_review(c, True)
    _at(clock, date(2026, 3, 5))
    p.answer_review(c, True)
    assert p.reviews[c].stage == 2
    _at(clock, date(2026, 3, 12))
    p.answer_review(c, False)
    r = p.reviews[c]
    assert r.stage == 1 and r.streak == 0 and r.due == date(2026, 3, 15)
    p.answer_review(c, False)
    assert p.reviews[c].stage == 0 and p.reviews[c].due == date(2026, 3, 13)
    p.answer_review(c, False)
    assert p.reviews[c].stage == 0  # floor


def test_add_review_from_mock_and_tracing_no_daily_cap():
    p, clock = planner(40, exam=date(2026, 4, 10))
    chars = [h.char for h in p.remaining()]
    for c in chars[:30]:
        p.add_review(c, "mock_exam_wrong")
    p.add_review(chars[30], "tracing_answer_seen")
    _at(clock, date(2026, 3, 2))
    assert len(p.due_reviews()) == 31  # none capped
    assert p.reviews[chars[30]].reason == "tracing_answer_seen"
    with pytest.raises(PlanError):
        p.add_review("A", "mock_exam_wrong")
    with pytest.raises(PlanError):
        p.add_review(chars[0], "bogus")


def test_add_review_existing_item_and_mastered_reentry():
    p, clock = planner(10, exam=date(2026, 3, 10))
    c = p.plan_for_today().new_chars[0]
    p.add_review(c, "mock_exam_wrong")
    for d in (2, 5, 12):
        _at(clock, date(2026, 3, d))
        p.answer_review(c, True)
    assert p.reviews[c].mastered
    p.add_review(c, "tracing_answer_seen")
    r = p.reviews[c]
    assert not r.mastered and r.stage == 0 and r.due == date(2026, 3, 13)
    _at(clock, date(2026, 3, 13))
    assert c in [x.char for x in p.due_reviews()]


def test_exam_date_change_recomputes_today():
    p, _ = planner(60, exam=date(2026, 3, 10))
    assert p.plan_for_today().quota == 6
    p.set_exam_date(date(2026, 3, 5))
    assert p.plan_for_today().quota == 12
