"""7-day refund: automatic decision + operator review.

* Eligible window: 7 days from the payment confirmation (KST clock injected).
* "Paid-feature use" = any PaidUsageEvent (over-free-limit chatbot question, mock exam start, or
  detailed-report view) for ANY child linked to the subscription, since the payment.
  Events are recorded at use time against the limit setting in force then, so the judgement
  follows the setting of that moment.
* No use inside the window -> immediate full refund via Toss + subscription ended at once.
* Use found (or window passed) -> rejected with a reason; the parent may ask for operator review;
  the operator sees the usage records and approves (refund + end) or rejects.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

KST = timezone(timedelta(hours=9))
REFUND_WINDOW = timedelta(days=7)

AUTO_APPROVED = "auto_approved"
AUTO_REJECTED = "auto_rejected"
REVIEW_REQUESTED = "review_requested"
OPERATOR_APPROVED = "operator_approved"
OPERATOR_REJECTED = "operator_rejected"
STATUSES = (AUTO_APPROVED, AUTO_REJECTED, REVIEW_REQUESTED, OPERATOR_APPROVED, OPERATOR_REJECTED)

FEATURE_LABEL = {"chatbot": "무료 한도를 넘은 챗봇 질문", "mockexam": "무료 한도를 넘은 모의고사 시작",
                 "detailed_report": "상세 리포트 열람"}


class RefundError(Exception):
    code = "refund_error"


class NothingToRefund(RefundError):
    code = "nothing_to_refund"


class NotFound(RefundError):
    code = "not_found"


class BadState(RefundError):
    code = "bad_state"


@dataclass
class RefundRequest:
    id: str
    parent_id: str
    payment_id: str
    payment_key: str
    amount: int
    requested_at: datetime
    status: str
    reason: str = ""
    usage_records: List[dict] = field(default_factory=list)
    decided_at: Optional[datetime] = None
    operator_note: str = ""


class RefundService:
    def __init__(self, billing, usage, clock: Callable[[], datetime] = lambda: datetime.now(KST),
                 notifier=None) -> None:
        self.billing, self.usage, self.clock, self.notifier = billing, usage, clock, notifier
        self.requests: Dict[str, RefundRequest] = {}

    # ---- judgement -----------------------------------------------------
    def usage_records(self, parent_id: str, since: datetime) -> List[dict]:
        sub = self.billing.get(parent_id)
        kids = set(sub.child_ids) if sub else set()
        out = [{"child_id": e.child_id, "feature": e.feature, "at": e.at.astimezone(KST).isoformat(),
                "label": FEATURE_LABEL.get(e.feature, e.feature)}
               for e in self.usage.paid_events if e.child_id in kids and e.at >= since]
        return sorted(out, key=lambda r: r["at"])

    def _do_refund(self, r: RefundRequest) -> None:
        pay = next(p for p in self.billing.payments if p.id == r.payment_id)
        self.billing.gateway.cancel_payment(pay.payment_key, "7일 이내 전액 환불")  # may raise
        pay.refunded = True
        sub = self.billing.get(r.parent_id)
        if sub is not None and sub.status != "ended":
            self.billing.terminate(r.parent_id)

    # ---- parent --------------------------------------------------------
    def request_refund(self, parent_id: str) -> RefundRequest:
        pay = self.billing.latest_payment(parent_id)
        if pay is None:
            raise NothingToRefund("환불할 결제가 없어요.")
        for r in self.requests.values():  # one decision per payment
            if r.payment_id == pay.id:
                return r
        now = self.clock()
        r = RefundRequest(uuid.uuid4().hex, parent_id, pay.id, pay.payment_key, pay.amount, now, AUTO_REJECTED)
        if now - pay.paid_at > REFUND_WINDOW:
            r.reason = "결제 후 7일이 지나 환불할 수 없어요."
        else:
            r.usage_records = self.usage_records(parent_id, pay.paid_at)
            if r.usage_records:
                kinds = sorted({x["label"] for x in r.usage_records})
                r.reason = ("유료 기능을 사용한 기록이 있어요: " + ", ".join(kinds)
                            + f" (총 {len(r.usage_records)}건).")
            else:
                self._do_refund(r)  # gateway failure propagates; nothing is recorded, so retry is possible
                r.status, r.decided_at = AUTO_APPROVED, now
        self.requests[r.id] = r
        return r

    def request_review(self, parent_id: str, request_id: str) -> RefundRequest:
        r = self._get(request_id)
        if r.parent_id != parent_id:
            raise NotFound()
        if r.status != AUTO_REJECTED:
            raise BadState("검토를 요청할 수 없는 상태예요.")
        r.status = REVIEW_REQUESTED
        return r

    def list_for_parent(self, parent_id: str) -> List[RefundRequest]:
        return [r for r in self.requests.values() if r.parent_id == parent_id]

    # ---- operator ------------------------------------------------------
    def pending_reviews(self) -> List[RefundRequest]:
        return [r for r in self.requests.values() if r.status == REVIEW_REQUESTED]

    def all_requests(self, status: Optional[str] = None) -> List[RefundRequest]:
        return [r for r in self.requests.values() if status is None or r.status == status]

    def approve(self, request_id: str, note: str = "") -> RefundRequest:
        r = self._get(request_id)
        if r.status != REVIEW_REQUESTED:
            raise BadState("검토 요청 상태가 아니에요.")
        self._do_refund(r)
        r.status, r.decided_at, r.operator_note = OPERATOR_APPROVED, self.clock(), note
        self._tell(r, "환불이 승인되어 전액 환불됐어요.")
        return r

    def reject(self, request_id: str, note: str = "") -> RefundRequest:
        r = self._get(request_id)
        if r.status != REVIEW_REQUESTED:
            raise BadState("검토 요청 상태가 아니에요.")
        r.status, r.decided_at, r.operator_note = OPERATOR_REJECTED, self.clock(), note
        self._tell(r, "운영자 검토 결과 환불이 거절됐어요." + (f" 사유: {note}" if note else ""))
        return r

    def _get(self, request_id: str) -> RefundRequest:
        r = self.requests.get(request_id)
        if r is None:
            raise NotFound()
        return r

    def _tell(self, r: RefundRequest, msg: str) -> None:
        if self.notifier:
            self.notifier.notify(r.parent_id, "refund_decided", {"message": msg, "status": r.status})
