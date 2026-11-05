import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from accounts.service import AccountService, FakeIdentityVerifier, Forbidden  # noqa: E402
from screentime import (  # noqa: E402
    ScreenTimeService, AppLocked, InvalidParentPin, ParentPinLocked, SettingsInvalid,
)

KST = timezone(timedelta(hours=9))


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t

    def adv(self, **kw):
        self.t += timedelta(**kw)


@pytest.fixture
def env():
    clock = Clock(datetime(2026, 3, 2, 15, 0, tzinfo=KST))
    v = FakeIdentityVerifier()
    acc = AccountService(v, clock=clock)
    p = acc.signup_parent(v.complete_verification("ci1"), "a@b.co", True, True, True)
    child = acc.create_child(p.id, "민수", "1234")
    other = acc.signup_parent(v.complete_verification("ci2"), "c@d.co", True, True, True)
    exam = {"on": False}
    pushes = []
    st = ScreenTimeService(acc, clock, exam_in_progress=lambda c: exam["on"],
                           notify_parent=lambda pid, k, d: pushes.append((pid, k, d)))
    st.set_parent_pin(p.id, "9999")
    return st, clock, p, child, other, exam, pushes


def use(st, clock, child, minutes):
    st.heartbeat(child.id)
    for _ in range(minutes):
        clock.adv(minutes=1)
        st.heartbeat(child.id)


def test_daily_limit_warning_then_lock(env):
    st, clock, p, c, *_ = env
    st.set_limits(p.id, c.id, daily_minutes=30)
    use(st, clock, c, 24)
    s = st.status(c.id)
    assert not s["warning"] and not s["locked"]
    use(st, clock, c, 1)  # 25 min used -> 5 min left
    s = st.status(c.id)
    assert s["warning"] and s["seconds_left"] == 300 and not s["locked"]
    use(st, clock, c, 5)
    s = st.status(c.id)
    assert s["locked"] and s["reason"] == "daily_limit"
    with pytest.raises(AppLocked):
        st.assert_can_use(c.id)


def test_daily_reset_at_kst_midnight(env):
    st, clock, p, c, *_ = env
    st.set_limits(p.id, c.id, daily_minutes=1)
    use(st, clock, c, 2)
    assert st.status(c.id)["locked"]
    clock.t = datetime(2026, 3, 2, 23, 59, 59, tzinfo=KST)
    assert st.status(c.id)["locked"]
    clock.t = datetime(2026, 3, 3, 0, 0, 0, tzinfo=KST)
    assert not st.status(c.id)["locked"]


def test_allowed_window(env):
    st, clock, p, c, *_ = env
    st.set_limits(p.id, c.id, windows=[("15:00", "16:00"), ("19:00", "20:00")])
    clock.t = datetime(2026, 3, 2, 15, 56, tzinfo=KST)
    assert st.status(c.id)["warning"]
    clock.t = datetime(2026, 3, 2, 16, 0, tzinfo=KST)
    s = st.status(c.id)
    assert s["locked"] and s["reason"] == "outside_window"
    clock.t = datetime(2026, 3, 2, 19, 0, tzinfo=KST)
    assert not st.status(c.id)["locked"]
    clock.t = datetime(2026, 3, 2, 8, 0, tzinfo=KST)
    assert st.status(c.id)["locked"]


def test_lock_deferred_during_mock_exam(env):
    st, clock, p, c, _, exam, _ = env
    st.set_limits(p.id, c.id, daily_minutes=10)
    exam["on"] = True
    use(st, clock, c, 12)
    s = st.status(c.id)
    assert not s["locked"] and s["lock_pending"]
    st.assert_can_use(c.id)  # exam keeps working
    exam["on"] = False       # exam finished
    s = st.status(c.id)
    assert s["locked"] and not s["lock_pending"]
    assert not st.can_start_exam(c.id)


def test_parent_pin_extra_time(env):
    st, clock, p, c, *_ = env
    st.set_limits(p.id, c.id, daily_minutes=5)
    use(st, clock, c, 6)
    assert st.status(c.id)["locked"]
    with pytest.raises(InvalidParentPin):
        st.grant_extra_time(p.id, c.id, "0000", 10)
    assert st.status(c.id)["locked"]
    s = st.grant_extra_time(p.id, c.id, "9999", 10)
    assert not s["locked"] and s["seconds_left"] == 600
    use(st, clock, c, 10)
    assert st.status(c.id)["locked"]


def test_extra_time_overrides_window(env):
    st, clock, p, c, *_ = env
    st.set_limits(p.id, c.id, windows=[("09:00", "10:00")])
    assert st.status(c.id)["locked"]
    s = st.grant_extra_time(p.id, c.id, "9999", 20)
    assert not s["locked"] and s["seconds_left"] == 1200
    clock.adv(minutes=21)
    assert st.status(c.id)["locked"]


def test_parent_pin_lockout_and_ownership(env):
    st, clock, p, c, other, *_ = env
    for _ in range(5):
        with pytest.raises(InvalidParentPin):
            st.grant_extra_time(p.id, c.id, "1111", 5)
    with pytest.raises(ParentPinLocked):
        st.grant_extra_time(p.id, c.id, "9999", 5)
    st.set_parent_pin(other.id, "4444")
    with pytest.raises(Forbidden):
        st.grant_extra_time(other.id, c.id, "4444", 5)
    with pytest.raises(Forbidden):
        st.set_limits(other.id, c.id, daily_minutes=10)


def test_extension_request_push_approve_reject(env):
    st, clock, p, c, other, _, pushes = env
    st.set_limits(p.id, c.id, daily_minutes=5)
    use(st, clock, c, 6)
    r = st.request_extension(c.id, 15)
    assert pushes[-1][0] == p.id and pushes[-1][1] == "extension_request"
    assert pushes[-1][2]["request_id"] == r.id
    assert st.request_extension(c.id, 15).id == r.id and len(pushes) == 1
    assert [x.id for x in st.pending_requests(p.id)] == [r.id]
    with pytest.raises(Forbidden):
        st.resolve_request(other.id, r.id, True)
    st.resolve_request(p.id, r.id, True)
    assert st.request_status(c.id, r.id) == "approved"
    assert not st.status(c.id)["locked"]
    use(st, clock, c, 16)
    assert st.status(c.id)["locked"]
    r2 = st.request_extension(c.id, 10)
    st.resolve_request(p.id, r2.id, False)
    assert st.request_status(c.id, r2.id) == "rejected"
    assert st.status(c.id)["locked"]


def test_invalid_settings_and_no_limits(env):
    st, clock, p, c, *_ = env
    assert st.status(c.id)["locked"] is False
    with pytest.raises(SettingsInvalid):
        st.set_limits(p.id, c.id, daily_minutes=0)
    with pytest.raises(SettingsInvalid):
        st.set_limits(p.id, c.id, windows=[("10:00", "09:00")])
    with pytest.raises(SettingsInvalid):
        st.set_limits(p.id, c.id, windows=[("09:00", "11:00"), ("10:00", "12:00")])
