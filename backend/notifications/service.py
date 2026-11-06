"""Parent notifications: in-app inbox + real Web Push (VAPID) + email fallback.

Every ``notify(parent_id, kind, payload)`` of the services (safety alerts, extension requests, usage-limit
upgrade prompts, weekly summary, billing, dormancy) goes through ``NotificationCenter``:

1. it is always stored in the parent's in-app inbox (``GET /api/parents/me/notifications``);
2. it is pushed to every PushSubscription the parent registered (Web Push, RFC 8030/8291/8292 via
   ``WebPushSender``). A 404/410 answer means the browser dropped the subscription: it is deleted;
3. if nothing was delivered (no subscription, or every push failed) an email is sent instead (Seed
   assumption: parents who cannot receive web push get email), except for kinds whose sender already emails.

Privacy: the push/email text is built only from whitelisted, system-generated fields (``push_message``);
a child's chat text never reaches a push service (SafetyEvent payloads do not contain it in the first place).
Push subscriptions belong to parent accounts only, so subscription prompts never reach a child's screen.
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import itertools
import logging
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Protocol
from urllib.parse import urlparse

log = logging.getLogger("hanja.notify")
KST = timezone(timedelta(hours=9))
GONE = (404, 410)
MAX_SUBSCRIPTIONS_PER_PARENT = 10
# The billing service emails these itself (payment failure: push AND email per Seed), so no duplicate.
EMAILED_BY_SENDER = frozenset({"payment_failed", "subscription_ended"})

TITLES = {
    "safety_event": "안전 알림",
    "extension_request": "사용 시간 연장 요청",
    "weekly_summary": "주간 학습 요약",
    "usage_limit_reached": "무료 한도 도달",
    "dormancy_notice": "휴면 전환 예정 안내",
    "payment_failed": "자동결제 실패",
    "subscription_ended": "구독 종료",
    "refund_decided": "환불 결과",
    "push_test": "알림 테스트",
}
CATEGORY = {"pii": "개인정보", "profanity": "욕설", "violence": "폭력·위험", "sexual": "성적 내용",
            "self_harm": "자해", "drugs": "약물", "harmful": "유해"}


class NotificationError(Exception):
    code = "notification_error"


class InvalidSubscription(NotificationError):
    code = "invalid_subscription"


@dataclass
class PushSubscription:
    parent_id: str
    endpoint: str
    p256dh: str
    auth: str
    created_at: datetime
    last_status: Optional[int] = None

    @property
    def key(self) -> str:
        return hashlib.sha256(self.endpoint.encode()).hexdigest()

    def info(self) -> dict:
        """pywebpush subscription_info."""
        return {"endpoint": self.endpoint, "keys": {"p256dh": self.p256dh, "auth": self.auth}}


@dataclass
class InboxItem:
    id: str
    seq: int
    parent_id: str
    kind: str
    payload: dict
    created_at: datetime
    child_id: Optional[str] = None
    push: str = "pending"        # pending | sent | failed | no_subscription | disabled
    email: bool = False


class PushSender(Protocol):
    def send(self, sub: PushSubscription, message: dict) -> int:
        """Deliver one encrypted Web Push message; return the push service's HTTP status."""


class FakePushSender:
    """Tests: records every message; ``status`` maps endpoint -> HTTP status (default 201)."""

    def __init__(self, status: Optional[Dict[str, int]] = None) -> None:
        self.status = dict(status or {})
        self.sent: List[tuple] = []  # (endpoint, message)

    def send(self, sub: PushSubscription, message: dict) -> int:
        self.sent.append((sub.endpoint, message))
        return self.status.get(sub.endpoint, 201)


def _b64url_bytes(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def validate_subscription(endpoint: str, p256dh: str, auth: str, allow_local: bool) -> None:
    """Shape checks + SSRF guard: https push-service URLs only (http://localhost only outside production)."""
    u = urlparse(endpoint or "")
    host = (u.hostname or "").lower()
    local = host in ("localhost", "127.0.0.1", "::1")
    if not host or (u.scheme != "https" and not (allow_local and local and u.scheme == "http")):
        raise InvalidSubscription("endpoint must be an https URL of a push service")
    if not allow_local:
        if local:
            raise InvalidSubscription("local endpoints are not allowed")
        try:
            if not ipaddress.ip_address(host).is_global:
                raise InvalidSubscription("private address")
        except ValueError:
            pass  # a host name, as push services use
    try:
        key, secret = _b64url_bytes(p256dh), _b64url_bytes(auth)
    except Exception as e:  # noqa: BLE001
        raise InvalidSubscription("keys must be base64url") from e
    if len(key) != 65 or key[0] != 4 or len(secret) != 16:
        raise InvalidSubscription("p256dh must be an uncompressed P-256 point and auth 16 bytes")


def push_message(item: InboxItem, nickname: Optional[str] = None) -> dict:
    """The only data that leaves the server: title/body from whitelisted system fields (never chat text)."""
    p, k = item.payload, item.kind
    who = nickname or "아이"
    if k == "safety_event":
        body = f"{who}의 챗봇 대화에서 {CATEGORY.get(p.get('category'), '유해')} 입력이 차단됐어요."
        if p.get("locked_today"):
            body += " 같은 대화에서 3회 반복되어 오늘 챗봇이 잠겼어요."
    elif k == "extension_request":
        body = f"{p.get('nickname') or who}이(가) {int(p.get('minutes') or 0)}분 더 쓰고 싶어해요. 눌러서 허락하거나 거절하세요."
    elif k == "dormancy_notice":
        body = f"1년 동안 접속하지 않아 {p.get('dormant_on', '')}에 휴면 계정으로 바뀌어요. 로그인하면 유지돼요."
    elif k in ("usage_limit_reached", "weekly_summary", "payment_failed", "subscription_ended",
               "refund_decided", "push_test"):
        body = str(p.get("message") or "")  # system-generated text (parent_notice etc.)
    else:
        body = "새 알림이 있어요."
    return {"title": TITLES.get(k, "한자 튜터"), "body": body[:300], "kind": k, "id": item.id,
            "tag": f"{k}-{item.seq}", "url": "/?alerts=1"}


class NotificationCenter:
    def __init__(
        self,
        sender: Optional[PushSender] = None,
        email=None,                                           # send(to, subject, body)
        email_of: Callable[[str], Optional[str]] = lambda pid: None,
        nickname_of: Callable[[str], Optional[str]] = lambda cid: None,
        clock: Callable[[], datetime] = lambda: datetime.now(KST),
        executor: Optional[ThreadPoolExecutor] = None,        # None = deliver inline (tests)
        allow_local_endpoints: bool = False,
        after_deliver: Optional[Callable[[], None]] = None,   # e.g. persist the delivery status
    ) -> None:
        self.after_deliver = after_deliver
        self.sender, self.email, self.email_of, self.nickname_of = sender, email, email_of, nickname_of
        self.clock, self.executor, self.allow_local = clock, executor, allow_local_endpoints
        self.inbox: List[InboxItem] = []
        self.subscriptions: Dict[str, PushSubscription] = {}  # sha256(endpoint) -> subscription
        self._seq = itertools.count()
        self._lock = threading.RLock()
        self._pending: List[Future] = []

    # ---- compatibility view (tests and older callers read .sent) -------------------
    @property
    def sent(self) -> List[tuple]:
        return [(i.parent_id, i.kind, i.payload) for i in self.inbox]

    @property
    def push_enabled(self) -> bool:
        return self.sender is not None

    # ---- subscriptions ----------------------------------------------------------------
    def subscribe(self, parent_id: str, endpoint: str, p256dh: str, auth: str) -> PushSubscription:
        validate_subscription(endpoint, p256dh, auth, self.allow_local)
        sub = PushSubscription(parent_id, endpoint, p256dh, auth, self.clock())
        with self._lock:
            old = self.subscriptions.get(sub.key)
            if old is not None and old.parent_id != parent_id:
                del self.subscriptions[sub.key]  # same browser, other parent logged in: re-owned
            mine = [s for s in self.subscriptions.values() if s.parent_id == parent_id and s.key != sub.key]
            for s in sorted(mine, key=lambda s: s.created_at)[: max(0, len(mine) - MAX_SUBSCRIPTIONS_PER_PARENT + 1)]:
                del self.subscriptions[s.key]
            self.subscriptions[sub.key] = sub
        return sub

    def unsubscribe(self, parent_id: str, endpoint: str) -> bool:
        key = hashlib.sha256((endpoint or "").encode()).hexdigest()
        with self._lock:
            s = self.subscriptions.get(key)
            if s is None or s.parent_id != parent_id:
                return False
            del self.subscriptions[key]
            return True

    def subscriptions_of(self, parent_id: str) -> List[PushSubscription]:
        with self._lock:
            return [s for s in self.subscriptions.values() if s.parent_id == parent_id]

    def has_push(self, parent_id: str) -> bool:
        return self.push_enabled and bool(self.subscriptions_of(parent_id))

    # ---- sending ------------------------------------------------------------------------
    def notify(self, parent_id: str, kind: str, payload: dict) -> InboxItem:
        item = InboxItem(uuid.uuid4().hex, next(self._seq), parent_id, kind, dict(payload or {}), self.clock(),
                         child_id=(payload or {}).get("child_id"))
        with self._lock:
            self.inbox.append(item)
        if self.executor is None:
            self._deliver(item)
        else:
            with self._lock:
                self._pending = [f for f in self._pending if not f.done()]
                self._pending.append(self.executor.submit(self._deliver, item))
        return item

    def _deliver(self, item: InboxItem) -> None:
        msg = push_message(item, self.nickname_of(item.child_id) if item.child_id else None)
        delivered = False
        if self.sender is None:
            item.push = "disabled"
        else:
            subs = self.subscriptions_of(item.parent_id)
            item.push = "no_subscription" if not subs else "failed"
            for s in subs:
                try:
                    status = int(self.sender.send(s, msg))
                except Exception as e:  # noqa: BLE001 - network errors: fall back, never fail the caller
                    log.warning("web push failed (%s)", type(e).__name__)
                    status = 0
                s.last_status = status
                if 200 <= status < 300:
                    delivered = True
                elif status in GONE:  # the browser unsubscribed: drop it
                    with self._lock:
                        self.subscriptions.pop(s.key, None)
            if delivered:
                item.push = "sent"
        if not delivered and item.kind not in EMAILED_BY_SENDER and self.email is not None:
            to = self.email_of(item.parent_id)
            if to:
                self.email.send(to, msg["title"], msg["body"])
                item.email = True
        if self.executor is not None and self.after_deliver is not None:
            try:
                self.after_deliver()
            except Exception:  # noqa: BLE001
                log.exception("persisting delivery status failed")

    def wait_idle(self, timeout: float = 10.0) -> None:
        """Wait for background deliveries (tests / flush before shutdown)."""
        with self._lock:
            pending = list(self._pending)
        for f in pending:
            f.result(timeout=timeout)

    # ---- inbox ---------------------------------------------------------------------------
    def inbox_for(self, parent_id: str) -> List[InboxItem]:
        with self._lock:
            return [i for i in self.inbox if i.parent_id == parent_id]

    def restore_seq(self) -> None:
        start = max((i.seq for i in self.inbox), default=-1) + 1
        self._seq = itertools.count(start)

    # ---- deletion ------------------------------------------------------------------------
    def purge_child(self, child_id: str) -> None:
        with self._lock:
            self.inbox[:] = [i for i in self.inbox if i.child_id != child_id]

    def purge_parent(self, parent_id: str) -> None:
        with self._lock:
            self.inbox[:] = [i for i in self.inbox if i.parent_id != parent_id]
            for k in [k for k, s in self.subscriptions.items() if s.parent_id == parent_id]:
                del self.subscriptions[k]
