import sys
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from billing import FakeTossGateway, PaymentFailed  # noqa: E402
from refunds import (  # noqa: E402
    AUTO_APPROVED, AUTO_REJECTED, OPERATOR_APPROVED, OPERATOR_REJECTED, REVIEW_REQUESTED, RefundService,
)
from refunds.api import create_app  # noqa: E402
from refunds.service import BadState, NothingToRefund  # noqa: E402
from usage import CHATBOT, MOCKEXAM  # noqa: E402
from test_billing import setup  # noqa: E402


def make(limits=None):
    bill, bot, usage, p, a, b, clock, gw, mail, push = setup(limits or {CHATBOT: 1, MOCKEXAM: 1})
    ref = RefundService(bill, usage, clock, notifier=push)
    bill.subscribe(p.id, "monthly", "k")
    return ref, bill, usage, p, a, b, clock, gw


def test_no_paid_use_within_7_days_refunds_fully_and_ends_now():
    ref, bill, usage, p, a, b, clock, gw = make()
    usage.consume(a.id, CHATBOT)  # within the free limit: not paid use
    clock.t += timedelta(days=6, hours=23)
    r = ref.request_refund(p.id)
    assert r.status == AUTO_APPROVED
    assert gw.cancels == [{"payment_key": bill.payments[0].payment_key, "reason": "7일 이내 전액 환불"}]
    assert bill.payments[0].refunded and bill.get(p.id).status == "ended"
    assert not bill.is_paid(a.id) and not bill.is_paid(b.id)


@pytest.mark.parametrize("kind", ["chatbot", "mockexam", "report"])
@pytest.mark.parametrize("child", ["a", "b"])  # any linked child counts
def test_any_childs_paid_use_rejects_with_reason(kind, child):
    ref, bill, usage, p, a, b, clock, gw = make()
    c = a if child == "a" else b
    clock.t += timedelta(days=2)
    if kind == "chatbot":
        usage.consume(c.id, CHATBOT)
        usage.consume(c.id, CHATBOT)
    elif kind == "mockexam":
        usage.consume(c.id, MOCKEXAM)
        usage.consume(c.id, MOCKEXAM)
    else:
        usage.record_paid_event(c.id, "detailed_report")
    r = ref.request_refund(p.id)
    assert r.status == AUTO_REJECTED and r.reason and r.usage_records
    assert gw.cancels == [] and bill.is_parent_paid(p.id)


def test_failed_response_released_is_not_paid_use():
    ref, bill, usage, p, a, b, clock, gw = make()
    usage.consume(a.id, CHATBOT)
    res = usage.consume(a.id, CHATBOT)  # beyond free limit...
    usage.release(res)  # ...but the response failed
    assert ref.request_refund(p.id).status == AUTO_APPROVED


def test_after_7_days_is_rejected_even_without_use():
    ref, bill, usage, p, a, b, clock, gw = make()
    clock.t += timedelta(days=7, seconds=1)
    r = ref.request_refund(p.id)
    assert r.status == AUTO_REJECTED and "7일" in r.reason and gw.cancels == []


def test_repeat_request_returns_same_decision_and_no_double_refund():
    ref, bill, usage, p, a, b, clock, gw = make()
    ref.request_refund(p.id)
    with pytest.raises(NothingToRefund):
        ref.request_refund(p.id)
    assert len(gw.cancels) == 1
    # a rejected decision is returned as-is on repeat
    ref2, _, usage2, p2, a2, *_ = make()
    usage2.record_paid_event(a2.id, "detailed_report")
    r = ref2.request_refund(p2.id)
    assert ref2.request_refund(p2.id) is r


def test_gateway_failure_keeps_subscription_and_allows_retry():
    ref, bill, usage, p, a, b, clock, gw = make()
    gw.fail_cancel = True
    with pytest.raises(PaymentFailed):
        ref.request_refund(p.id)
    assert bill.is_parent_paid(p.id) and not ref.requests
    gw.fail_cancel = False
    assert ref.request_refund(p.id).status == AUTO_APPROVED


def test_usage_before_payment_window_start_is_not_counted():
    ref, bill, usage, p, a, b, clock, gw = make()
    # event stamped before the payment instant must not count
    usage.record_paid_event(a.id, "detailed_report")
    usage.paid_events[0].at = bill.payments[0].paid_at - timedelta(days=1)
    assert ref.request_refund(p.id).status == AUTO_APPROVED


def test_review_flow_operator_approves_with_refund():
    ref, bill, usage, p, a, b, clock, gw = make()
    usage.record_paid_event(b.id, "detailed_report")
    r = ref.request_refund(p.id)
    with pytest.raises(BadState):
        ref.approve(r.id)  # not yet requested
    ref.request_review(p.id, r.id)
    assert r.status == REVIEW_REQUESTED and ref.pending_reviews() == [r]
    ref.approve(r.id, "ok")
    assert r.status == OPERATOR_APPROVED and len(gw.cancels) == 1 and bill.get(p.id).status == "ended"


def test_review_flow_operator_rejects_keeps_subscription():
    ref, bill, usage, p, a, b, clock, gw = make()
    usage.record_paid_event(a.id, "detailed_report")
    r = ref.request_refund(p.id)
    ref.request_review(p.id, r.id)
    ref.reject(r.id, "사용 기록 확인")
    assert r.status == OPERATOR_REJECTED and gw.cancels == [] and bill.is_parent_paid(p.id)
    with pytest.raises(BadState):
        ref.request_review(p.id, r.id)


def test_http_parent_and_admin_screen():
    ref, bill, usage, p, a, b, clock, gw = make()
    usage.record_paid_event(a.id, "detailed_report")
    app = create_app(ref, lambda tok: p.id if tok == "good" else (_ for _ in ()).throw(KeyError()), "adm")
    c = TestClient(app)
    h = {"Authorization": "Bearer good"}
    assert c.post("/refunds").status_code == 401
    r = c.post("/refunds", headers=h)
    assert r.status_code == 201 and r.json()["status"] == "auto_rejected" and r.json()["reason"]
    assert r.json()["can_request_review"]
    rid = r.json()["id"]
    assert c.post(f"/refunds/{rid}/review", headers=h).json()["status"] == "review_requested"
    assert c.get("/admin/refunds").status_code == 403
    assert c.get("/admin/refunds", headers={"X-Admin-Token": "bad"}).status_code == 403
    adm = {"X-Admin-Token": "adm"}
    lst = c.get("/admin/refunds?status=review_requested", headers=adm).json()
    assert len(lst) == 1 and lst[0]["usage_records"][0]["feature"] == "detailed_report"
    assert "환불 검토" in c.get("/admin/refunds-ui").text
    assert c.post(f"/admin/refunds/{rid}/approve", headers=adm, json={"note": "x"}).json()["status"] == \
        "operator_approved"
    assert c.post(f"/admin/refunds/{rid}/approve", headers=adm, json={}).status_code == 400
    assert len(gw.cancels) == 1


def test_http_auto_refund():
    ref, bill, usage, p, a, b, clock, gw = make()
    c = TestClient(create_app(ref, lambda tok: p.id, "adm"))
    r = c.post("/refunds", headers={"Authorization": "Bearer t"})
    assert r.json()["status"] == "auto_approved" and len(gw.cancels) == 1
