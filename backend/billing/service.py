"""Subscription billing.

* Two plans (monthly / yearly); prices come from configuration (mapping / env), not code.
* Toss Payments billing (정기결제): issue billing key from authKey, then charge it.
  `TossHttpGateway` talks to the real API (test mode = test_sk_ secret key); `FakeTossGateway`
  is for tests/dev. The gateway is an interface.
* Entitlement is granted the instant the backend receives the payment confirmation
  (`confirmed_at`), far inside the 1-minute requirement.
* One subscription per parent covers up to 3 children.
* Cancelling keeps paid features until the end of the paid period.
* Renewal failure -> 3-day grace, daily retry, push + email each failure, then free.
* Paid -> free transition zeroes the children's current day/week counters.
* Every KST/clock decision uses the injected clock.
"""
from __future__ import annotations

import base64
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Mapping, Optional, Protocol

KST = timezone(timedelta(hours=9))
MAX_CHILDREN = 3
GRACE_DAYS = 3
ACTIVE, CANCEL_SCHEDULED, GRACE, ENDED = "active", "cancel_scheduled", "grace", "ended"
UNLOCK_SLA = timedelta(minutes=1)
DEFAULT_PRICES = {"monthly": 4900, "yearly": 49000}
PRICE_ENV = {"monthly": "BILLING_MONTHLY_PRICE", "yearly": "BILLING_YEARLY_PRICE"}


class BillingError(Exception):
    code = "billing_error"


class PaymentFailed(BillingError):
    code = "payment_failed"


class NotSubscribed(BillingError):
    code = "not_subscribed"


class InvalidPlan(BillingError):
    code = "invalid_plan"


class TooManyChildren(BillingError):
    code = "too_many_children"


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    price_krw: int
    months: int


@dataclass
class BillingConfig:
    prices: Dict[str, int] = field(default_factory=lambda: dict(DEFAULT_PRICES))

    @classmethod
    def from_mapping(cls, m: Optional[Mapping[str, int]] = None) -> "BillingConfig":
        p = dict(DEFAULT_PRICES)
        for k, v in (m or {}).items():
            if k not in DEFAULT_PRICES or int(v) <= 0:
                raise ValueError(f"bad price {k}={v}")
            p[k] = int(v)
        return cls(p)

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "BillingConfig":
        env = os.environ if env is None else env
        return cls.from_mapping({k: int(env[e]) for k, e in PRICE_ENV.items() if e in env})

    def plans(self) -> List[Plan]:
        return [Plan("monthly", "월간", self.prices["monthly"], 1),
                Plan("yearly", "연간", self.prices["yearly"], 12)]

    def plan(self, plan_id: str) -> Plan:
        for p in self.plans():
            if p.id == plan_id:
                return p
        raise InvalidPlan(plan_id)


# ---- gateway ---------------------------------------------------------------
class TossGateway(Protocol):
    def issue_billing_key(self, auth_key: str, customer_key: str) -> str: ...
    def charge(self, billing_key: str, customer_key: str, amount: int, order_id: str,
               order_name: str, customer_email: str) -> str:
        """Return paymentKey; raise PaymentFailed."""
    def cancel_payment(self, payment_key: str, reason: str) -> None:
        """Full refund of a payment; raise PaymentFailed."""


class FakeTossGateway:
    def __init__(self) -> None:
        self.fail = False
        self.charges: List[dict] = []
        self.issued: List[str] = []
        self.fail_cancel = False
        self.cancels: List[dict] = []

    def issue_billing_key(self, auth_key: str, customer_key: str) -> str:
        if not auth_key:
            raise PaymentFailed("bad authKey")
        self.issued.append(customer_key)
        return "bk_" + uuid.uuid4().hex[:10]

    def charge(self, billing_key, customer_key, amount, order_id, order_name, customer_email) -> str:
        if self.fail:
            raise PaymentFailed("card declined")
        self.charges.append({"billing_key": billing_key, "amount": amount, "order_id": order_id})
        return "pk_" + uuid.uuid4().hex[:10]

    def cancel_payment(self, payment_key: str, reason: str) -> None:
        if self.fail_cancel:
            raise PaymentFailed("cancel failed")
        self.cancels.append({"payment_key": payment_key, "reason": reason})


class TossHttpGateway:
    """Real Toss Payments billing API. Test mode when the secret key starts with test_sk_."""
    BASE = "https://api.tosspayments.com"

    def __init__(self, secret_key: Optional[str] = None,
                 post: Optional[Callable[[str, dict, dict], dict]] = None) -> None:
        self.secret_key = secret_key or os.environ.get("TOSS_SECRET_KEY", "")
        self._post = post or self._urllib_post

    def _headers(self) -> dict:
        tok = base64.b64encode(f"{self.secret_key}:".encode()).decode()
        return {"Authorization": f"Basic {tok}", "Content-Type": "application/json"}

    def _urllib_post(self, url: str, headers: dict, body: dict) -> dict:
        import urllib.error
        import urllib.request
        req = urllib.request.Request(url, json.dumps(body).encode(), headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("message", "")
            except Exception:
                msg = ""
            raise PaymentFailed(msg or f"http {e.code}")
        except OSError as e:
            raise PaymentFailed(str(e))

    def issue_billing_key(self, auth_key: str, customer_key: str) -> str:
        d = self._post(f"{self.BASE}/v1/billing/authorizations/issue", self._headers(),
                       {"authKey": auth_key, "customerKey": customer_key})
        return d["billingKey"]

    def charge(self, billing_key, customer_key, amount, order_id, order_name, customer_email) -> str:
        d = self._post(f"{self.BASE}/v1/billing/{billing_key}", self._headers(), {
            "customerKey": customer_key, "amount": amount, "orderId": order_id,
            "orderName": order_name, "customerEmail": customer_email})
        if d.get("status") not in (None, "DONE"):
            raise PaymentFailed(d.get("message", "not done"))
        return d["paymentKey"]


    def cancel_payment(self, payment_key: str, reason: str) -> None:
        self._post(f"{self.BASE}/v1/payments/{payment_key}/cancel", self._headers(),
                   {"cancelReason": reason})


class RecordingEmail:
    def __init__(self) -> None:
        self.sent: List[dict] = []

    def send(self, to: str, subject: str, body: str) -> None:
        self.sent.append({"to": to, "subject": subject, "body": body})


# ---- records ---------------------------------------------------------------
@dataclass
class Payment:
    id: str
    parent_id: str
    payment_key: str
    amount: int
    plan_id: str
    paid_at: datetime
    order_id: str
    refunded: bool = False


@dataclass
class Subscription:
    id: str
    parent_id: str
    plan_id: str
    billing_key: str
    customer_key: str
    status: str
    period_start: datetime
    period_end: datetime
    confirmed_at: datetime
    child_ids: List[str] = field(default_factory=list)
    grace_end: Optional[datetime] = None
    next_retry_at: Optional[datetime] = None
    failures: int = 0
    ended_at: Optional[datetime] = None


def add_months(t: datetime, months: int) -> datetime:
    k = t.astimezone(KST)
    y, m = divmod(k.month - 1 + months, 12)
    y, m = k.year + y, m + 1
    d = k.day
    while True:
        try:
            return k.replace(year=y, month=m, day=d)
        except ValueError:
            d -= 1


class BillingService:
    def __init__(
        self,
        gateway: TossGateway,
        config: Optional[BillingConfig] = None,
        clock: Callable[[], datetime] = lambda: datetime.now(KST),
        parent_info: Optional[Callable[[str], tuple]] = None,  # parent_id -> (email, [child ids])
        usage=None,  # UsageService (for counter reset)
        notifier=None,  # push: notify(parent_id, kind, payload)
        email=None,  # email: send(to, subject, body)
    ) -> None:
        self.gateway, self.config, self.clock = gateway, config or BillingConfig(), clock
        self.parent_info = parent_info or (lambda pid: ("", []))
        self.usage, self.notifier, self.email = usage, notifier, email
        self.subs: Dict[str, Subscription] = {}  # parent_id -> subscription
        self.payments: List[Payment] = []

    # ---- status interface used by UsageService ------------------------
    def _sub_for_child(self, child_id: str) -> Optional[Subscription]:
        for s in self.subs.values():
            if child_id in s.child_ids:
                return s
        return None

    def _entitled(self, s: Subscription, now: datetime) -> bool:
        if s.status == ENDED:
            return False
        if s.status == CANCEL_SCHEDULED:
            return now < s.period_end
        if s.status == GRACE:
            return now < s.grace_end
        return True  # active (renewal pending is processed by run_due)

    def is_paid(self, child_id: str) -> bool:
        s = self._sub_for_child(child_id)
        return bool(s and self._entitled(s, self.clock()))

    def is_parent_paid(self, parent_id: str) -> bool:
        s = self.subs.get(parent_id)
        return bool(s and self._entitled(s, self.clock()))

    # ---- queries ------------------------------------------------------
    def plans(self) -> List[dict]:
        return [{"id": p.id, "name": p.name, "price_krw": p.price_krw, "months": p.months}
                for p in self.config.plans()]

    def get(self, parent_id: str) -> Optional[Subscription]:
        return self.subs.get(parent_id)

    # ---- subscribe ----------------------------------------------------
    def subscribe(self, parent_id: str, plan_id: str, auth_key: str,
                  child_ids: Optional[List[str]] = None) -> Subscription:
        plan = self.config.plan(plan_id)
        email, kids = self.parent_info(parent_id)
        kids = list(child_ids if child_ids is not None else kids)
        if len(kids) > MAX_CHILDREN:
            raise TooManyChildren()
        cur = self.subs.get(parent_id)
        if cur and self._entitled(cur, self.clock()):
            raise BillingError("already subscribed")
        customer_key = f"parent_{parent_id}"
        billing_key = self.gateway.issue_billing_key(auth_key, customer_key)
        order_id = uuid.uuid4().hex
        pkey = self.gateway.charge(billing_key, customer_key, plan.price_krw, order_id,
                                   f"한자튜터 {plan.name} 구독", email)
        now = self.clock()  # payment confirmation received
        self.payments.append(Payment(uuid.uuid4().hex, parent_id, pkey, plan.price_krw, plan.id, now, order_id))
        sub = Subscription(uuid.uuid4().hex, parent_id, plan.id, billing_key, customer_key, ACTIVE,
                           now, add_months(now, plan.months), now, kids)
        self.subs[parent_id] = sub
        return sub

    def set_children(self, parent_id: str, child_ids: List[str]) -> Subscription:
        s = self._need(parent_id)
        if len(set(child_ids)) > MAX_CHILDREN:
            raise TooManyChildren()
        s.child_ids = list(dict.fromkeys(child_ids))
        return s

    def link_child(self, parent_id: str, child_id: str) -> None:
        s = self._need(parent_id)
        if child_id in s.child_ids:
            return
        if len(s.child_ids) >= MAX_CHILDREN:
            raise TooManyChildren()
        s.child_ids.append(child_id)

    def _need(self, parent_id: str) -> Subscription:
        s = self.subs.get(parent_id)
        if s is None or s.status == ENDED:
            raise NotSubscribed()
        return s

    # ---- cancel -------------------------------------------------------
    def cancel(self, parent_id: str) -> Subscription:
        """Stop renewal; paid features stay until the end of the paid period."""
        s = self._need(parent_id)
        if s.status == ACTIVE:
            s.status = CANCEL_SCHEDULED
        elif s.status == GRACE:
            self._end(s)
        return s

    def terminate(self, parent_id: str) -> Subscription:
        """End the subscription immediately (refund): free plan, counters reset."""
        s = self._need(parent_id)
        self._end(s)
        return s

    def latest_payment(self, parent_id: str) -> Optional[Payment]:
        mine = [p for p in self.payments if p.parent_id == parent_id and not p.refunded]
        return max(mine, key=lambda p: p.paid_at) if mine else None

    # ---- renewal / grace / downgrade ---------------------------------------
    def run_due(self) -> List[str]:
        """Process renewals, retries, scheduled cancellations. Call periodically (daily or more)."""
        now = self.clock()
        done: List[str] = []
        for s in list(self.subs.values()):
            if s.status == CANCEL_SCHEDULED and now >= s.period_end:
                self._end(s)
                done.append(f"{s.parent_id}:ended")
            elif s.status == ACTIVE and now >= s.period_end:
                done.append(f"{s.parent_id}:{self._renew(s, now)}")
            elif s.status == GRACE and now >= s.next_retry_at:
                done.append(f"{s.parent_id}:{self._retry(s, now)}")
        return done

    def _charge_renewal(self, s: Subscription, now: datetime) -> bool:
        plan = self.config.plan(s.plan_id)
        email, _ = self.parent_info(s.parent_id)
        order_id = uuid.uuid4().hex
        try:
            pkey = self.gateway.charge(s.billing_key, s.customer_key, plan.price_krw, order_id,
                                       f"한자튜터 {plan.name} 구독", email)
        except PaymentFailed:
            return False
        self.payments.append(Payment(uuid.uuid4().hex, s.parent_id, pkey, plan.price_krw, plan.id, now, order_id))
        s.status, s.grace_end, s.next_retry_at, s.failures = ACTIVE, None, None, 0
        s.period_start = s.period_end
        s.period_end = add_months(s.period_start, plan.months)
        s.confirmed_at = now
        return True

    def _renew(self, s: Subscription, now: datetime) -> str:
        if self._charge_renewal(s, now):
            return "renewed"
        s.status = GRACE
        s.failures = 1
        s.grace_end = s.period_end + timedelta(days=GRACE_DAYS)
        s.next_retry_at = now + timedelta(days=1)
        self._notify_failure(s)
        return "grace"

    def _retry(self, s: Subscription, now: datetime) -> str:
        if self._charge_renewal(s, now):
            return "renewed"
        s.failures += 1
        if now >= s.grace_end:
            self._end(s)
            return "ended"
        s.next_retry_at = now + timedelta(days=1)
        self._notify_failure(s)
        return "grace"

    def _end(self, s: Subscription) -> None:
        was_ended = s.status == ENDED
        s.status, s.ended_at = ENDED, self.clock()
        s.grace_end = s.next_retry_at = None
        if was_ended:
            return
        if self.usage is not None:  # paid -> free: today's/this week's counters back to 0
            for cid in s.child_ids:
                self.usage.reset_counters(cid)
        self._alert(s, "subscription_ended", "구독이 종료되어 무료 플랜으로 전환됐어요.",
                    "구독 종료 안내")

    def _notify_failure(self, s: Subscription) -> None:
        left = max(0, (s.grace_end - self.clock()).days)
        self._alert(s, "payment_failed",
                    f"자동결제에 실패했어요. 유예 기간 {left}일 남음. 결제 수단을 확인해 주세요.",
                    "자동결제 실패 안내")

    def _alert(self, s: Subscription, kind: str, msg: str, subject: str) -> None:
        if self.notifier:
            self.notifier.notify(s.parent_id, kind, {"message": msg, "failures": s.failures})
        if self.email:
            to, _ = self.parent_info(s.parent_id)
            if to:
                self.email.send(to, subject, msg)

    # ---- helpers for tests / API --------------------------------------
    def unlock_within_sla(self, parent_id: str) -> bool:
        s = self.subs[parent_id]
        return self.clock() - s.confirmed_at <= UNLOCK_SLA and self.is_parent_paid(parent_id)
