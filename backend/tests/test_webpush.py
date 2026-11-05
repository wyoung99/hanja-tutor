"""Web Push (VAPID) for parents: AC11 extension request push, AC12 weekly push summary (subscribers only),
AC8 safety alerts, AC13 parent-only upgrade prompts; inbox + email fallback; privacy of push payloads.

Unit tests use a fake push sender. ``test_real_webpush_request_*`` runs the real pywebpush sender against a
local mock push service and checks the VAPID JWT signature and decrypts the aes128gcm body with the
"browser" key -- nothing is sent to real browser vendors.
"""
import base64
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from fastapi.testclient import TestClient

from app.main import Clock, create_app
from billing import RecordingEmail
from notifications import (FakePushSender, InvalidSubscription, NotificationCenter, PushSubscription, VapidConfig,
                           WebPushSender, generate_vapid_keys, validate_subscription)

KST = timezone(timedelta(hours=9))
T0 = datetime(2026, 11, 2, 10, 0, tzinfo=KST)  # Monday
PHONE = "010-1234-5678"
VAPID = generate_vapid_keys()
ENV = {"VAPID_PUBLIC_KEY": VAPID["public_key"], "VAPID_PRIVATE_KEY": VAPID["private_key"],
       "VAPID_SUBJECT": "mailto:ops@example.com", "ADMIN_TOKEN": "adm"}


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def browser_keys():
    """What PushManager.subscribe() produces in a browser: an ECDH P-256 key pair and a 16-byte auth secret."""
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return priv, b64u(pub), b64u(b"0123456789abcdef")


def make(sender=None, env=None):
    """sender=None -> a FakePushSender; sender=False -> no injected sender (VAPID env decides)."""
    sender = FakePushSender() if sender is None else sender
    clock = Clock(T0)
    app = create_app(clock=clock, env={**ENV, **(env or {})}, push_sender=sender or None)
    return TestClient(app), clock, app.state.services, sender


def login(c, ci="ci-push"):
    tok = c.post("/api/dev/verify", json={"ci": ci}).json()["verification_token"]
    c.post("/api/parents/signup", json={"verification_token": tok, "email": f"{ci}@x.kr", "agree_terms": True,
                                        "agree_privacy": True, "agree_legal_guardian": True})
    tok = c.post("/api/dev/verify", json={"ci": ci}).json()["verification_token"]
    return {"Authorization": "Bearer " + c.post("/api/parents/login", json={"verification_token": tok}).json()["session"]}


def kid(c, H, nick="별이"):
    cid = c.post("/api/children", headers=H, json={"nickname": nick, "pin": "1234", "ai_consent": True}).json()["id"]
    c.put(f"/api/children/{cid}/target", headers=H, json={"body": "korea_eomunhoe", "grade": "8"})
    return cid


def subscribe(c, H, endpoint="https://fcm.googleapis.com/fcm/send/abc"):
    _, pub, auth = browser_keys()
    r = c.post("/api/parents/me/push-subscriptions", headers=H, json={"endpoint": endpoint, "keys": {"p256dh": pub, "auth": auth}})
    assert r.status_code == 201, r.text
    return endpoint


# ---- configuration / subscription flow ---------------------------------------------------------------
def test_vapid_public_key_endpoint_and_parent_only_subscription():
    c, _, svc, _ = make()
    cfg = c.get("/api/push/config").json()
    assert cfg == {"enabled": True, "public_key": VAPID["public_key"]}
    assert len(unb64u(cfg["public_key"])) == 65  # applicationServerKey for PushManager.subscribe
    _, pub, auth = browser_keys()
    body = {"endpoint": "https://fcm.googleapis.com/fcm/send/x", "keys": {"p256dh": pub, "auth": auth}}
    assert c.post("/api/parents/me/push-subscriptions", json=body).status_code == 401  # children have no session
    H = login(c)
    assert c.post("/api/parents/me/push-subscriptions", headers=H, json=body).status_code == 201
    assert c.post("/api/parents/me/push-subscriptions", headers=H, json=body).status_code == 201  # idempotent
    assert len(c.get("/api/parents/me/push-subscriptions", headers=H).json()) == 1
    bad = {**body, "keys": {"p256dh": "short", "auth": auth}}
    assert c.post("/api/parents/me/push-subscriptions", headers=H, json=bad).status_code == 422
    assert c.request("DELETE", "/api/parents/me/push-subscriptions", headers=H,
                     json={"endpoint": body["endpoint"]}).json() == {"ok": True}
    assert c.get("/api/parents/me/push-subscriptions", headers=H).json() == []


def test_push_disabled_without_vapid_keys_inbox_still_works():
    c, _, svc, _ = make(sender=False, env={"VAPID_PUBLIC_KEY": "", "VAPID_PRIVATE_KEY": ""})
    assert c.get("/api/push/config").json() == {"enabled": False, "public_key": None}
    H = login(c)
    _, pub, auth = browser_keys()
    r = c.post("/api/parents/me/push-subscriptions", headers=H,
               json={"endpoint": "https://fcm.googleapis.com/fcm/send/x", "keys": {"p256dh": pub, "auth": auth}})
    assert r.status_code == 409
    cid = kid(c, H)
    c.post(f"/api/children/{cid}/extension-requests", json={"minutes": 10})
    notes = c.get("/api/parents/me/notifications", headers=H).json()
    assert notes[-1]["kind"] == "extension_request" and notes[-1]["push"] == "disabled" and notes[-1]["email"] is True


def test_endpoint_validation_blocks_non_push_urls_in_production():
    _, pub, auth = browser_keys()
    validate_subscription("https://updates.push.services.mozilla.com/wpush/v2/x", pub, auth, allow_local=False)
    for bad in ("http://fcm.googleapis.com/x", "https://127.0.0.1/x", "https://10.0.0.5/x", "https://localhost/x",
                "ftp://x/y", ""):
        with pytest.raises(InvalidSubscription):
            validate_subscription(bad, pub, auth, allow_local=False)
    validate_subscription("http://127.0.0.1:9999/push", pub, auth, allow_local=True)  # local E2E only


# ---- AC11: extension request -> parent push ----------------------------------------------------------
def test_extension_request_is_pushed_to_the_parent_and_resolvable():
    c, _, svc, sender = make()
    H = login(c)
    ep = subscribe(c, H)
    cid = kid(c, H)
    req = c.post(f"/api/children/{cid}/extension-requests", json={"minutes": 15}).json()
    (endpoint, msg), = sender.sent
    assert endpoint == ep and msg["kind"] == "extension_request" and msg["title"] == "사용 시간 연장 요청"
    assert "별이" in msg["body"] and "15분" in msg["body"] and msg["url"].startswith("/")
    note = c.get("/api/parents/me/notifications", headers=H).json()[-1]
    assert note["push"] == "sent" and note["email"] is False
    assert c.post(f"/api/parents/me/extension-requests/{req['id']}", headers=H, json={"approve": True}).json()["status"] == "approved"


# ---- AC8: safety alert push, never with the child's text ---------------------------------------------
def test_safety_alert_push_never_contains_the_childs_message():
    c, _, svc, sender = make()
    H = login(c)
    subscribe(c, H)
    cid = kid(c, H)
    secrets = [f"내 전화번호는 {PHONE}이야", "우리 집 주소는 서울시 강남구 테헤란로 123이야", "이 병신 같은 지랄"]
    tid = None
    for m in secrets:  # same conversation -> the 3rd block locks the chatbot for the day
        tid = c.post(f"/api/children/{cid}/chat", json={"message": m, "thread_id": tid}).json()["thread_id"]
    pushed = [m for _, m in sender.sent if m["kind"] == "safety_event"]
    assert len(pushed) == 3 and "오늘 챗봇이 잠겼어요" in pushed[-1]["body"]  # 3rd in one conversation
    blob = json.dumps([m for _, m in sender.sent], ensure_ascii=False)
    blob += json.dumps(c.get("/api/parents/me/notifications", headers=H).json(), ensure_ascii=False)
    blob += json.dumps(svc["mail"].sent, ensure_ascii=False)
    for needle in (PHONE, "01012345678", "1234-5678", "테헤란로", "강남구", "병신", "지랄"):
        assert needle not in blob
    assert set(pushed[0]) == {"title", "body", "kind", "id", "tag", "url"}  # whitelisted fields only


# ---- AC13: upgrade prompt goes to the parent's push, the child only sees a friendly notice -------------
def test_usage_limit_upgrade_prompt_is_parent_push_only():
    c, _, svc, sender = make(env={"USAGE_CHATBOT_DAILY_LIMIT": "1"})
    H = login(c)
    subscribe(c, H)
    cid = kid(c, H)
    assert c.post(f"/api/children/{cid}/chat", json={"message": "學이 뭐예요?"}).json()["status"] == "answered"
    child_view = c.post(f"/api/children/{cid}/chat", json={"message": "校는 뭐예요?"}).json()
    assert child_view["status"] == "locked" and "구독" not in child_view["reply"]
    up = [m for _, m in sender.sent if m["kind"] == "usage_limit_reached"]
    assert len(up) == 1 and "구독" in up[0]["body"] and "별이" in up[0]["body"]
    c.post(f"/api/children/{cid}/chat", json={"message": "또요"})
    assert len([m for _, m in sender.sent if m["kind"] == "usage_limit_reached"]) == 1  # once per period


# ---- AC12: weekly push summary to subscribers only -----------------------------------------------------
def test_weekly_push_summary_only_for_paying_parents():
    c, clock, svc, sender = make()
    Hp, Hf = login(c, "ci-paid"), login(c, "ci-free")
    ep_paid = subscribe(c, Hp, "https://fcm.googleapis.com/fcm/send/paid")
    ep_free = subscribe(c, Hf, "https://fcm.googleapis.com/fcm/send/free")
    kid(c, Hp, "유료"), kid(c, Hf, "무료")
    assert c.post("/api/billing/subscribe", headers=Hp, json={"plan_id": "monthly", "auth_key": "ak"}).status_code == 201
    out = c.post("/api/admin/jobs/weekly-summary", headers={"X-Admin-Token": "adm"}).json()
    assert len(out["sent"]) == 1
    weekly = [(e, m) for e, m in sender.sent if m["kind"] == "weekly_summary"]
    assert [e for e, _ in weekly] == [ep_paid] and "유료" in weekly[0][1]["body"]
    assert not any(e == ep_free for e, _ in weekly)
    c.post("/api/admin/jobs/weekly-summary", headers={"X-Admin-Token": "adm"})
    assert len([m for _, m in sender.sent if m["kind"] == "weekly_summary"]) == 1  # once per KST week


# ---- fallback: 410/404 drop the subscription, failures fall back to email ------------------------------
def test_gone_subscription_is_dropped_and_email_is_the_fallback():
    sender = FakePushSender({"https://fcm.googleapis.com/fcm/send/old": 410,
                             "https://fcm.googleapis.com/fcm/send/broken": 500})
    c, _, svc, _ = make(sender=sender)
    H = login(c)
    subscribe(c, H, "https://fcm.googleapis.com/fcm/send/old")
    subscribe(c, H, "https://fcm.googleapis.com/fcm/send/broken")
    cid = kid(c, H)
    c.post(f"/api/children/{cid}/extension-requests", json={"minutes": 5})
    note = c.get("/api/parents/me/notifications", headers=H).json()[-1]
    assert note["push"] == "failed" and note["email"] is True
    assert [s["endpoint_origin"] for s in c.get("/api/parents/me/push-subscriptions", headers=H).json()] == [
        "https://fcm.googleapis.com"]  # 410 one removed, 500 one kept for the next try
    assert svc["mail"].sent[-1]["to"] == "ci-push@x.kr" and "5분" in svc["mail"].sent[-1]["body"]
    # a parent without any subscription gets the email straight away
    H2 = login(c, "ci-nosub")
    cid2 = kid(c, H2, "달이")
    c.post(f"/api/children/{cid2}/extension-requests", json={"minutes": 7})
    note = c.get("/api/parents/me/notifications", headers=H2).json()[-1]
    assert note["push"] == "no_subscription" and note["email"] is True


def test_billing_failure_is_pushed_and_emailed_once_by_billing():
    center = NotificationCenter(FakePushSender(), email=RecordingEmail(), email_of=lambda p: "p@x.kr")
    center.notify("p", "payment_failed", {"message": "자동결제에 실패했어요."})
    assert center.email.sent == []  # billing itself emails payment failures; no duplicate from the fallback


# ---- real Web Push request against a local mock push service ----------------------------------------
class _PushService(BaseHTTPRequestHandler):
    status = 201
    received: list = []

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        type(self).received.append({"path": self.path, "headers": dict(self.headers), "body": body})
        self.send_response(type(self).status)
        self.end_headers()

    def log_message(self, *a):
        pass


@pytest.fixture
def push_service():
    _PushService.received, _PushService.status = [], 201
    srv = HTTPServer(("127.0.0.1", 0), _PushService)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}", _PushService
    srv.shutdown()


def _verify_vapid_jwt(token: str, public_key_b64: str, audience: str) -> dict:
    head, claims, sig = token.split(".")
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), unb64u(public_key_b64))
    raw = unb64u(sig)
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    pub.verify(der, f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))  # raises if forged
    c = json.loads(unb64u(claims))
    assert json.loads(unb64u(head))["alg"] == "ES256" and c["aud"] == audience and c["sub"] == "mailto:ops@example.com"
    return c


def test_real_webpush_request_is_vapid_signed_and_decryptable(push_service):
    import http_ece

    base, svc = push_service
    priv, pub, auth = browser_keys()
    sender = WebPushSender(VapidConfig.from_env(ENV))
    sub = PushSubscription("p1", f"{base}/push/abc", pub, auth, datetime.now(KST))
    msg = {"title": "사용 시간 연장 요청", "body": "별이이(가) 15분 더 쓰고 싶어해요.", "kind": "extension_request",
           "id": "x", "tag": "extension_request-1", "url": "/?alerts=1"}
    assert sender.send(sub, msg) == 201
    (req,), = [svc.received]
    h = {k.lower(): v for k, v in req["headers"].items()}
    assert req["path"] == "/push/abc" and h["content-encoding"] == "aes128gcm" and int(h["ttl"]) > 0
    assert h["urgency"] == "high"
    scheme, params = h["authorization"].split(" ", 1)
    kv = dict(p.strip().split("=", 1) for p in params.split(","))
    assert scheme == "vapid" and kv["k"] == VAPID["public_key"]
    _verify_vapid_jwt(kv["t"], VAPID["public_key"], base)
    plain = http_ece.decrypt(req["body"], private_key=priv, auth_secret=unb64u(auth), version="aes128gcm")
    assert json.loads(plain) == msg  # only the whitelisted message, readable only by the browser


def test_real_sender_410_drops_subscription_through_the_center(push_service):
    base, svc = push_service
    svc.status = 410
    _, pub, auth = browser_keys()
    center = NotificationCenter(WebPushSender(VapidConfig.from_env(ENV)), email=RecordingEmail(),
                                email_of=lambda p: "p@x.kr", allow_local_endpoints=True)
    center.subscribe("p1", f"{base}/push/gone", pub, auth)
    item = center.notify("p1", "extension_request", {"child_id": "c", "nickname": "별이", "minutes": 5})
    assert len(svc.received) == 1 and item.push == "failed" and item.email is True
    assert center.subscriptions_of("p1") == []
