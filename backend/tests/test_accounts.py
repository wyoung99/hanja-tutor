import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from accounts import service as s  # noqa: E402
from accounts.api import create_app  # noqa: E402


@pytest.fixture
def env():
    v = s.FakeIdentityVerifier()
    svc = s.AccountService(v, clock=lambda: datetime(2026, 1, 1, tzinfo=s.KST))
    return v, svc


def signup(v, svc, ci="ci-1"):
    tok = v.complete_verification(ci)
    return svc.signup_parent(tok, "p@example.com", True, True, True)


def test_signup_requires_phone_verification(env):
    v, svc = env
    with pytest.raises(s.IdentityNotVerified):
        svc.signup_parent("bogus", "p@example.com", True, True, True)
    assert svc.parents == {}


def test_signup_requires_consent_and_email(env):
    v, svc = env
    tok = v.complete_verification("ci")
    with pytest.raises(s.ConsentRequired):
        svc.signup_parent(tok, "p@example.com", True, False, True)
    with pytest.raises(s.ValidationFailed):
        svc.signup_parent(tok, "nope", True, True, True)
    assert svc.parents == {}


def test_signup_ok_blocks_duplicates_and_token_reuse(env):
    v, svc = env
    p = signup(v, svc)
    assert p.email == "p@example.com" and "ci-1" not in repr(p)
    with pytest.raises(s.AlreadyRegistered):
        signup(v, svc)
    tok = v.complete_verification("ci-2")
    svc.signup_parent(tok, "q@example.com", True, True, True)
    with pytest.raises(s.IdentityNotVerified):
        svc.signup_parent(tok, "r@example.com", True, True, True)


def test_child_profile_nickname_pin(env):
    v, svc = env
    p = signup(v, svc)
    c = svc.create_child(p.id, "하늘", "1234", ai_consent=True)
    assert c.nickname == "하늘" and c.pin_hash != "1234"
    with pytest.raises(s.ValidationFailed):
        svc.create_child(p.id, "별", "12")
    with pytest.raises(s.ValidationFailed):
        svc.create_child(p.id, "별", "abcd")
    with pytest.raises(s.ValidationFailed):
        svc.create_child(p.id, "", "1234")
    svc.create_child(p.id, "a", "1234")
    svc.create_child(p.id, "b", "1234")
    with pytest.raises(s.ValidationFailed):
        svc.create_child(p.id, "c", "1234")


def test_no_ai_consent_disables_only_chatbot(env):
    v, svc = env
    p = signup(v, svc)
    c = svc.create_child(p.id, "하늘", "1234", ai_consent=False)
    f = svc.features(c.id)
    assert f["chatbot"] is False
    assert all(f[k] for k in f if k != "chatbot")
    with pytest.raises(s.ChatbotDisabled):
        svc.require_chatbot(c.id)
    svc.set_ai_consent(p.id, c.id, True)
    svc.require_chatbot(c.id)
    assert svc.features(c.id)["chatbot"] is True


def test_pin_locks_after_five_failures_and_parent_reset_unlocks(env):
    v, svc = env
    p = signup(v, svc)
    c = svc.create_child(p.id, "하늘", "1234")
    for i in range(4):
        with pytest.raises(s.InvalidPin) as e:
            svc.verify_pin(c.id, "0000")
        assert e.value.remaining == 4 - i
    with pytest.raises(s.ProfileLocked):
        svc.verify_pin(c.id, "0000")
    with pytest.raises(s.ProfileLocked):  # correct PIN rejected while locked
        svc.verify_pin(c.id, "1234")
    other = signup(v, svc, "ci-other")
    with pytest.raises(s.Forbidden):
        svc.reset_pin(other.id, c.id, "9999")
    svc.reset_pin(p.id, c.id, "9999")
    with pytest.raises(s.InvalidPin):
        svc.verify_pin(c.id, "1234")
    assert svc.verify_pin(c.id, "9999").failed_attempts == 0


def test_success_resets_failure_counter(env):
    v, svc = env
    p = signup(v, svc)
    c = svc.create_child(p.id, "하늘", "1234")
    for _ in range(4):
        with pytest.raises(s.InvalidPin):
            svc.verify_pin(c.id, "0000")
    svc.verify_pin(c.id, "1234")
    with pytest.raises(s.InvalidPin) as e:
        svc.verify_pin(c.id, "0000")
    assert e.value.remaining == 4


def test_http_flow(env):
    v, svc = env
    cl = TestClient(create_app(svc))
    body = {"verification_token": "x", "email": "p@example.com",
            "agree_terms": True, "agree_privacy": True, "agree_legal_guardian": True}
    assert cl.post("/parents/signup", json=body).status_code == 401
    tok = v.complete_verification("ci-http")
    body["verification_token"] = tok
    assert cl.post("/parents/signup", json=body).status_code == 201
    sess = cl.post("/parents/login", json={"verification_token": tok}).json()["session"]
    h = {"Authorization": f"Bearer {sess}"}
    assert cl.post("/children", json={"nickname": "n", "pin": "1234"}).status_code == 403
    cid = cl.post("/children", json={"nickname": "n", "pin": "1234"}, headers=h).json()["id"]
    assert cl.post(f"/children/{cid}/chat-access").status_code == 403
    assert cl.get(f"/children/{cid}/features").json()["todays_hanja"] is True
    for _ in range(4):
        assert cl.post(f"/children/{cid}/verify-pin", json={"pin": "0"}).status_code == 401
    assert cl.post(f"/children/{cid}/verify-pin", json={"pin": "0"}).status_code == 423
    assert cl.post(f"/children/{cid}/reset-pin", json={"pin": "4321"}, headers=h).status_code == 200
    assert cl.post(f"/children/{cid}/verify-pin", json={"pin": "4321"}).status_code == 200
    assert cl.put(f"/children/{cid}/ai-consent", json={"ai_consent": True}, headers=h).status_code == 200
    assert cl.post(f"/children/{cid}/chat-access").status_code == 200
