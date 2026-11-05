import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from accounts.service import FakeIdentityVerifier, NotFound  # noqa: E402
from conversations import ConversationService, InMemoryCheckpointer  # noqa: E402
from lifecycle import LifecycleService, PaymentRecord  # noqa: E402
from test_chatbot import setup  # noqa: E402

KST = timezone(timedelta(hours=9))
ANS = "學校는 배우는 곳이에요. 學은 배울 학이에요. 校는 학교 교예요."


def make():
    svc, acc, p, child, notifier, clock = setup(handler=lambda s, m: ANS)
    acc.clock = clock
    p.last_active_at = clock()
    cp = InMemoryCheckpointer()
    conv = ConversationService(svc, acc, cp)
    life = LifecycleService(acc, conv, notifier, clock=clock)
    return life, conv, svc, acc, p, child, notifier, clock, cp


def seed_data(life, conv, child, p):
    tid, _ = conv.send(child.id, "學校가 뭐야?")
    life.learning_stores[child.id] = {"learned": ["學"]}
    life.payments.add(PaymentRecord("pay1", p.id, 3900, life.clock()))
    return tid


def test_delete_child_leaves_no_records_and_keeps_siblings():
    life, conv, svc, acc, p, child, n, clock, cp = make()
    other = acc.create_child(p.id, "달이", "4321", ai_consent=True)
    tid = seed_data(life, conv, child, p)
    otid, _ = conv.send(other.id, "學校")
    life.delete_child(p.id, child.id)
    assert child.id not in acc.children
    assert tid not in cp.thread_ids() and tid not in conv.meta.rows
    assert child.id not in life.learning_stores
    assert not any(m.child_id == child.id for m in conv.meta.rows.values())
    assert otid in cp.thread_ids() and other.id in acc.children


def test_delete_account_removes_everything_and_archives_payments():
    life, conv, svc, acc, p, child, n, clock, cp = make()
    tid = seed_data(life, conv, child, p)
    life.delete_account(p.id)
    assert p.id not in acc.parents and not acc.children
    assert cp.thread_ids() == [] and conv.meta.rows == {}
    assert life.learning_stores == {}
    assert life.payments.records == {}  # gone from operational store
    assert [a.record.id for a in life.archive.rows] == ["pay1"]
    assert life.archive.rows[0].delete_after.year == clock().year + 5
    assert life.archive.purge_expired(clock() + timedelta(days=365 * 5 - 5)) == 0
    assert life.archive.purge_expired(clock() + timedelta(days=365 * 5 + 5)) == 1
    with pytest.raises(NotFound):
        life.delete_account(p.id)


def test_dormancy_notice_30_days_before_then_dormant_then_restore():
    life, conv, svc, acc, p, child, notifier, clock, cp = make()
    tid = seed_data(life, conv, child, p)
    t0 = clock()
    clock.t = t0 + timedelta(days=300)
    assert life.run_dormancy_sweep() == {"notified": [], "dormant": [], "deleted": []}
    clock.t = t0 + timedelta(days=335)
    r = life.run_dormancy_sweep()
    assert r["notified"] == [p.id] and r["dormant"] == []
    kinds = [k for (_, k, _) in notifier.sent]
    assert kinds.count("dormancy_notice") == 1
    clock.t = t0 + timedelta(days=364)
    assert life.run_dormancy_sweep()["dormant"] == []
    assert p.id in acc.parents
    clock.t = t0 + timedelta(days=366)
    r = life.run_dormancy_sweep()
    assert r["dormant"] == [p.id] and r["notified"] == []
    assert p.id not in acc.parents and p.id in acc.dormant_parents
    assert child.id not in acc.children and child.id in acc.dormant_children
    assert cp.thread_ids() == [] and conv.meta.rows == {}  # conversations moved to separate storage
    assert life.payments.records  # payments untouched
    # re-login via identity verification restores everything
    ver = acc.verifier
    session = life.login_parent(ver.complete_verification("ci1"))
    assert acc.parent_for_session(session).id == p.id
    assert child.id in acc.children and tid in cp.thread_ids() and tid in conv.meta.rows


def test_login_resets_inactivity_clock_and_notice():
    life, conv, svc, acc, p, child, notifier, clock, cp = make()
    t0 = clock()
    clock.t = t0 + timedelta(days=340)
    life.run_dormancy_sweep()
    assert p.dormancy_notice_at is not None
    life.login_parent(acc.verifier.complete_verification("ci1"))
    assert p.dormancy_notice_at is None
    clock.t = t0 + timedelta(days=400)
    assert life.run_dormancy_sweep()["dormant"] == []


def test_late_first_sweep_still_gives_30_days_notice():
    life, conv, svc, acc, p, child, notifier, clock, cp = make()
    t0 = clock()
    clock.t = t0 + timedelta(days=500)
    r = life.run_dormancy_sweep()
    assert r["notified"] == [p.id] and r["dormant"] == []
    clock.t = t0 + timedelta(days=531)
    assert life.run_dormancy_sweep()["dormant"] == [p.id]


def test_dormant_deleted_after_one_year():
    life, conv, svc, acc, p, child, notifier, clock, cp = make()
    life.payments.add(PaymentRecord("pay1", p.id, 1000, clock()))
    t0 = clock()
    clock.t = t0 + timedelta(days=335)
    life.run_dormancy_sweep()
    clock.t = t0 + timedelta(days=366)
    life.run_dormancy_sweep()
    clock.t = t0 + timedelta(days=366 + 366)
    r = life.run_dormancy_sweep()
    assert r["deleted"] == [p.id]
    assert not acc.dormant_parents and not acc.dormant_children
    assert len(life.archive.rows) == 1
