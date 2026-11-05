import base64
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from billing import (  # noqa: E402
    ACTIVE, CANCEL_SCHEDULED, ENDED, GRACE, BillingConfig, BillingService, FakeTossGateway,
    PaymentFailed, RecordingEmail, TossHttpGateway,
)
from billing.api import create_app  # noqa: E402
from billing.service import TooManyChildren  # noqa: E402
from chatbot.service import RecordingNotifier  # noqa: E402
from usage import CHATBOT, MOCKEXAM, LimitReached, UsageLimitConfig, UsageService  # noqa: E402
from test_usage import world  # noqa: E402

KST = timezone(timedelta(hours=9))
T0 = datetime(2026, 11, 4, 10, 0, tzinfo=KST)  # Wednesday


class Late:
    """Subscription status that can be wired after the usage service exists."""
    def __init__(self):
        self.b = None

    def is_paid(self, cid):
        return self.b.is_paid(cid)


def setup(limits=None):
    late = Late()
    bot, usage, acc, p, a, b, clock, notifier = world(limits=limits, sub=late)
    gw, mail, push = FakeTossGateway(), RecordingEmail(), RecordingNotifier()
    bill = BillingService(
        gw, BillingConfig.from_mapping({"monthly": 5900, "yearly": 59000}), clock,
        parent_info=lambda pid: (p.email, [c.id for c in acc.children_of(pid)]),
        usage=usage, notifier=push, email=mail)
    late.b = bill
    return bill, bot, usage, p, a, b, clock, gw, mail, push


def test_plans_show_configured_prices():
    bill = setup()[0]
    assert {x["id"]: x["price_krw"] for x in bill.plans()} == {"monthly": 5900, "yearly": 59000}
    assert BillingConfig.from_env({"BILLING_MONTHLY_PRICE": "3000"}).prices["monthly"] == 3000
    assert BillingConfig.from_env({}).prices["yearly"] > 0
    with pytest.raises(ValueError):
        BillingConfig.from_mapping({"monthly": 0})


def test_toss_http_gateway_billing_flow_uses_toss_endpoints():
    calls = []

    def post(url, headers, body):
        calls.append((url, headers, body))
        if url.endswith("/issue"):
            return {"billingKey": "BK1"}
        return {"status": "DONE", "paymentKey": "PK1"}

    gw = TossHttpGateway("test_sk_abc", post)
    assert gw.issue_billing_key("AUTH", "cust") == "BK1"
    assert gw.charge("BK1", "cust", 5900, "o1", "name", "a@b.kr") == "PK1"
    assert calls[0][0] == "https://api.tosspayments.com/v1/billing/authorizations/issue"
    assert calls[1][0] == "https://api.tosspayments.com/v1/billing/BK1"
    assert calls[1][2]["amount"] == 5900
    assert base64.b64decode(calls[0][1]["Authorization"].split()[1]) == b"test_sk_abc:"
    bad = TossHttpGateway("k", lambda u, h, b: {"status": "ABORTED", "message": "x"})
    with pytest.raises(PaymentFailed):
        bad.charge("BK", "c", 1, "o", "n", "e")


def test_payment_unlocks_within_a_minute_for_three_children():
    bill, bot, usage, p, a, b, clock, gw, *_ = setup({CHATBOT: 1, MOCKEXAM: 1})
    c3 = bot.accounts.create_child(p.id, "해님", "1234", ai_consent=True)
    for c in (a, b, c3):
        c.extra["target"] = ("b", "8")
    assert [bot.ask(c.id, "t", "學이 뭐예요?").status for c in (a, b, c3)] == ["answered"] * 3
    assert usage.is_locked(a.id, CHATBOT) and usage.is_locked(c3.id, CHATBOT)
    bill.subscribe(p.id, "monthly", "authKey")
    assert gw.charges[0]["amount"] == 5900
    assert bill.unlock_within_sla(p.id)
    clock.t += timedelta(seconds=30)
    for c in (a, b, c3):
        assert not usage.is_locked(c.id, CHATBOT)
        usage.consume(c.id, CHATBOT)
        usage.consume(c.id, CHATBOT)  # beyond the free limit: allowed and recorded
    # each child had used its 1 free question; both new consumes are beyond the limit
    assert len(usage.paid_events) == 6
    assert len(bill.get(p.id).child_ids) == 3


def test_max_three_children_per_subscription():
    bill, bot, usage, p, a, b, *_ = setup()
    with pytest.raises(TooManyChildren):
        bill.subscribe(p.id, "monthly", "k", child_ids=["1", "2", "3", "4"])
    bill.subscribe(p.id, "yearly", "k")
    bill.link_child(p.id, "x3")
    with pytest.raises(TooManyChildren):
        bill.link_child(p.id, "x4")


def test_failed_first_payment_does_not_subscribe():
    bill, bot, usage, p, a, b, clock, gw, *_ = setup()
    gw.fail = True
    with pytest.raises(PaymentFailed):
        bill.subscribe(p.id, "monthly", "k")
    assert bill.get(p.id) is None and not bill.is_paid(a.id)


def test_cancel_keeps_access_until_period_end_then_free():
    bill, bot, usage, p, a, b, clock, gw, mail, push = setup({CHATBOT: 1, MOCKEXAM: 1})
    s = bill.subscribe(p.id, "monthly", "k")
    bill.cancel(p.id)
    assert s.status == CANCEL_SCHEDULED
    clock.t = s.period_end - timedelta(seconds=1)
    bill.run_due()
    assert bill.is_paid(a.id) and len(gw.charges) == 1  # no renewal charge
    clock.t = s.period_end
    bill.run_due()
    assert s.status == ENDED and not bill.is_paid(a.id)


def test_renewal_success_extends_period():
    bill, bot, usage, p, a, b, clock, gw, *_ = setup()
    s = bill.subscribe(p.id, "monthly", "k")
    end = s.period_end
    clock.t = end
    assert bill.run_due() == [f"{p.id}:renewed"]
    assert s.status == ACTIVE and s.period_end > end and len(gw.charges) == 2


def test_failed_renewal_three_day_grace_daily_retry_alerts_then_free():
    bill, bot, usage, p, a, b, clock, gw, mail, push = setup({CHATBOT: 1, MOCKEXAM: 1})
    s = bill.subscribe(p.id, "monthly", "k")
    end = s.period_end
    gw.fail = True
    clock.t = end
    bill.run_due()
    assert s.status == GRACE and s.grace_end == end + timedelta(days=3)
    assert bill.is_paid(a.id)  # still paid during grace
    assert len(mail.sent) == 1 and mail.sent[0]["to"] == p.email
    assert [k for _, k, _ in push.sent].count("payment_failed") == 1
    bill.run_due()  # same instant: no extra retry
    assert len(mail.sent) == 1
    for day in (1, 2):
        clock.t = end + timedelta(days=day)
        bill.run_due()
        assert s.status == GRACE and bill.is_paid(a.id)
        assert len(mail.sent) == 1 + day
    clock.t = end + timedelta(days=3)
    bill.run_due()
    assert s.status == ENDED and not bill.is_paid(a.id)
    assert [k for _, k, _ in push.sent].count("payment_failed") == 3
    assert mail.sent[-1]["subject"] == "구독 종료 안내"


def test_retry_success_during_grace_restores_active():
    bill, bot, usage, p, a, b, clock, gw, mail, push = setup()
    s = bill.subscribe(p.id, "monthly", "k")
    gw.fail = True
    clock.t = s.period_end
    bill.run_due()
    gw.fail = False
    clock.t += timedelta(days=1)
    assert bill.run_due() == [f"{p.id}:renewed"]
    assert s.status == ACTIVE and s.failures == 0


def test_paid_to_free_resets_counters_for_day_and_week():
    bill, bot, usage, p, a, b, clock, gw, *_ = setup({CHATBOT: 2, MOCKEXAM: 1})
    s = bill.subscribe(p.id, "monthly", "k")
    for _ in range(3):
        usage.consume(a.id, CHATBOT)
    usage.consume(a.id, MOCKEXAM)
    usage.consume(a.id, MOCKEXAM)
    usage.consume(b.id, CHATBOT)
    assert usage.snapshot(a.id, CHATBOT)["used"] == 3
    bill.cancel(p.id)
    clock.t = s.period_end
    bill.run_due()
    for c in (a, b):
        assert usage.snapshot(c.id, CHATBOT)["used"] == 0
        assert usage.snapshot(c.id, MOCKEXAM)["used"] == 0
    # counters really back to zero: free limit is available again
    usage.consume(a.id, CHATBOT)
    usage.consume(a.id, CHATBOT)
    with pytest.raises(LimitReached):
        usage.consume(a.id, CHATBOT)


def test_api_plans_subscribe_cancel():
    bill, bot, usage, p, a, b, *_ = setup()
    c = TestClient(create_app(bill, lambda tok: p.id if tok == "good" else (_ for _ in ()).throw(KeyError())))
    assert c.get("/billing/plans").json()[0]["price_krw"] == 5900
    assert c.post("/billing/subscribe", json={"plan_id": "monthly", "auth_key": "k"}).status_code == 401
    h = {"Authorization": "Bearer good"}
    r = c.post("/billing/subscribe", json={"plan_id": "monthly", "auth_key": "k"}, headers=h)
    assert r.status_code == 201 and r.json()["status"] == "active" and len(r.json()["child_ids"]) == 2
    assert c.post("/billing/cancel", headers=h).json()["status"] == "cancel_scheduled"
    assert c.post("/billing/subscribe", json={"plan_id": "bogus", "auth_key": "k"}, headers=h).status_code == 400
