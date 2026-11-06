"""AC2 accounts: parent id/password sign-up (no phone verification), parent-created child accounts, one login
for both roles, child lockout after 5 wrong passwords + parent reset, AI-consent gating, password hashing."""
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from accounts import service as s  # noqa: E402
from accounts.api import create_app  # noqa: E402

PW = "parent-pw-1"


@pytest.fixture
def svc():
    return s.AccountService(clock=lambda: datetime(2026, 1, 1, tzinfo=s.KST))


def signup(svc, login_id="mom01", email="p@example.com"):
    return svc.signup_parent(login_id, PW, email, True, True, True)


def test_signup_needs_no_phone_verification(svc):
    p = signup(svc)
    assert p.login_id == "mom01" and p.email == "p@example.com"
    assert p.consents == {"terms": True, "privacy": True, "legal_guardian": True}
    assert not hasattr(s, "FakeIdentityVerifier") and not hasattr(s, "IdentityNotVerified")
    r = svc.login("mom01", PW)
    assert r.role == s.PARENT and r.account_id == p.id


def test_signup_requires_consents_email_and_valid_credentials(svc):
    with pytest.raises(s.ConsentRequired):
        svc.signup_parent("mom01", PW, "p@example.com", True, False, True)
    with pytest.raises(s.ValidationFailed):
        svc.signup_parent("mom01", PW, "nope", True, True, True)
    for bad_id in ("ab", "a" * 21, "엄마아이디", "-mom", "mom 01"):
        with pytest.raises(s.ValidationFailed):
            svc.signup_parent(bad_id, PW, "p@example.com", True, True, True)
    for bad_pw in ("short1", "onlyletters", "12345678", "x1" * 70):
        with pytest.raises(s.ValidationFailed):
            svc.signup_parent("mom01", bad_pw, "p@example.com", True, True, True)
    assert svc.parents == {} and svc._login_index == {}


def test_duplicate_id_rejected_case_insensitive_across_parents_and_children(svc):
    p = signup(svc)
    with pytest.raises(s.AlreadyRegistered):
        signup(svc, "MOM01", "q@example.com")
    svc.create_child(p.id, "하늘", "sky1", login_id="Sky01")
    with pytest.raises(s.AlreadyRegistered):  # a parent cannot take a child's id
        signup(svc, "sky01", "q@example.com")
    with pytest.raises(s.AlreadyRegistered):  # and a child cannot take a parent's id
        svc.create_child(p.id, "별", "star1", login_id="Mom01")
    assert len(svc.parents) == 1 and len(svc.children) == 1


def test_passwords_are_hashed_never_plaintext(svc):
    p = signup(svc)
    c = svc.create_child(p.id, "하늘", "sky1234", login_id="sky01")
    for acct, pw in ((p, PW), (c, "sky1234")):
        assert pw not in repr(acct) and acct.password_hash.startswith("scrypt$16384$8$1$")
        assert s.check_password(pw, acct.password_hash) and not s.check_password(pw + "x", acct.password_hash)
    # per-password salt: the same password gives different hashes
    assert s.hash_password("same-pass1") != s.hash_password("same-pass1")
    assert not s.check_password("anything", None) and not s.check_password("x", "garbage")


def test_one_login_for_parent_and_child_wrong_password(svc):
    p = signup(svc)
    c = svc.create_child(p.id, "하늘", "sky1", login_id="sky01")
    rp = svc.login("MOM01", PW)  # ids are case-insensitive
    rc = svc.login("sky01", "sky1")
    assert (rp.role, rp.account_id) == (s.PARENT, p.id)
    assert (rc.role, rc.account_id, rc.parent_id, rc.nickname) == (s.CHILD, c.id, p.id, "하늘")
    assert svc.principal(rp.session) == (s.PARENT, p.id) and svc.principal(rc.session) == (s.CHILD, c.id)
    with pytest.raises(s.InvalidCredentials) as e:
        svc.login("mom01", "wrong-pass1")
    assert e.value.remaining is None  # parents are not locked
    with pytest.raises(s.InvalidCredentials):
        svc.login("nobody", PW)
    # role separation: a child session is refused on parent APIs, an unknown token needs login
    with pytest.raises(s.ParentOnly):
        svc.parent_for_session(rc.session)
    with pytest.raises(s.LoginRequired):
        svc.parent_for_session("bogus")
    assert svc.parent_for_session(rp.session).id == p.id
    svc.logout(rc.session)
    assert svc.principal(rc.session) is None


def test_children_are_created_by_the_parent_only(svc):
    p = signup(svc)
    c = svc.create_child(p.id, "하늘", "sky1", ai_consent=True, login_id="sky01")
    assert c.parent_id == p.id and c.login_id == "sky01"
    with pytest.raises(s.ValidationFailed):
        svc.create_child(p.id, "별", "abc", login_id="star01")  # child password >= 4
    with pytest.raises(s.ValidationFailed):
        svc.create_child(p.id, "", "1234", login_id="star01")
    with pytest.raises(s.NotFound):
        svc.create_child("not-a-parent", "별", "1234", login_id="star01")
    svc.create_child(p.id, "a", "1234", login_id="kid-a")
    svc.create_child(p.id, "b", "1234", login_id="kid-b")
    with pytest.raises(s.ValidationFailed):
        svc.create_child(p.id, "c", "1234", login_id="kid-c")  # max 3


def test_no_ai_consent_disables_only_chatbot(svc):
    p = signup(svc)
    c = svc.create_child(p.id, "하늘", "1234", ai_consent=False)
    f = svc.features(c.id)
    assert f["chatbot"] is False
    assert all(f[k] for k in f if k != "chatbot")
    with pytest.raises(s.ChatbotDisabled):
        svc.require_chatbot(c.id)
    svc.set_ai_consent(p.id, c.id, True)
    svc.require_chatbot(c.id)
    assert svc.features(c.id)["chatbot"] is True


def test_child_locks_after_five_wrong_passwords_and_parent_reset_unlocks(svc):
    p = signup(svc)
    c = svc.create_child(p.id, "하늘", "sky1", login_id="sky01")
    old = svc.login("sky01", "sky1").session
    for i in range(4):
        with pytest.raises(s.InvalidCredentials) as e:
            svc.login("sky01", "0000")
        assert e.value.remaining == 4 - i
    with pytest.raises(s.ProfileLocked):
        svc.login("sky01", "0000")
    assert c.locked
    with pytest.raises(s.ProfileLocked):  # correct password rejected while locked
        svc.login("sky01", "sky1")
    other = signup(svc, "dad01", "d@example.com")
    with pytest.raises(s.Forbidden):
        svc.reset_child_password(other.id, c.id, "new1")
    svc.reset_child_password(p.id, c.id, "new1")
    assert svc.principal(old) is None  # reset signs the child's devices out
    with pytest.raises(s.InvalidCredentials):
        svc.login("sky01", "sky1")
    assert svc.login("sky01", "new1").role == s.CHILD and c.failed_attempts == 0 and not c.locked


def test_success_resets_failure_counter(svc):
    p = signup(svc)
    c = svc.create_child(p.id, "하늘", "sky1", login_id="sky01")
    for _ in range(4):
        with pytest.raises(s.InvalidCredentials):
            svc.login("sky01", "0000")
    svc.login("sky01", "sky1")
    with pytest.raises(s.InvalidCredentials) as e:
        svc.login("sky01", "0000")
    assert e.value.remaining == 4 and c.failed_attempts == 1


def test_dormant_parent_login_restores_and_child_waits(svc):
    p = signup(svc)
    c = svc.create_child(p.id, "하늘", "sky1", login_id="sky01")
    svc.make_dormant(p.id)
    with pytest.raises(s.AlreadyRegistered):  # dormant ids stay reserved
        signup(svc, "mom01", "x@example.com")
    with pytest.raises(s.AccountDormant):
        svc.login("sky01", "sky1")
    r = svc.login("mom01", PW)
    assert r.restored_from_dormancy and p.id in svc.parents and c.id in svc.children
    assert svc.login("sky01", "sky1").role == s.CHILD


def test_http_flow(svc):
    cl = TestClient(create_app(svc))
    body = {"login_id": "mom01", "password": PW, "email": "p@example.com",
            "agree_terms": True, "agree_privacy": True, "agree_legal_guardian": True}
    assert cl.post("/parents/signup", json={**body, "agree_legal_guardian": False}).status_code == 400
    assert cl.post("/parents/signup", json=body).status_code == 201
    assert cl.post("/parents/signup", json={**body, "login_id": "MOM01"}).json()["detail"]["code"] == "login_id_taken"
    assert cl.post("/auth/login", json={"login_id": "mom01", "password": "nope-nope1"}).status_code == 401
    lp = cl.post("/auth/login", json={"login_id": "mom01", "password": PW}).json()
    assert lp["role"] == "parent"
    h = {"Authorization": f"Bearer {lp['session']}"}
    kid = {"login_id": "sky01", "password": "sky1", "nickname": "n"}
    assert cl.post("/children", json=kid).status_code == 401
    cid = cl.post("/children", json=kid, headers=h).json()["id"]
    lc = cl.post("/auth/login", json={"login_id": "sky01", "password": "sky1"}).json()
    assert lc["role"] == "child" and lc["account_id"] == cid and lc["nickname"] == "n"
    k = {"Authorization": f"Bearer {lc['session']}"}
    assert cl.get("/auth/me", headers=k).json()["role"] == "child"
    # a child session cannot create children, reset passwords or change consent (parent APIs -> 403)
    assert cl.post("/children", json={**kid, "login_id": "sky02"}, headers=k).status_code == 403
    assert cl.post(f"/children/{cid}/reset-password", json={"password": "hack"}, headers=k).status_code == 403
    assert cl.put(f"/children/{cid}/ai-consent", json={"ai_consent": True}, headers=k).status_code == 403
    assert cl.get(f"/children/{cid}/features").status_code == 401
    assert cl.post(f"/children/{cid}/chat-access", headers=k).status_code == 403  # no AI consent
    assert cl.get(f"/children/{cid}/features", headers=k).json()["todays_hanja"] is True
    for i in range(4):
        r = cl.post("/auth/login", json={"login_id": "sky01", "password": "0"})
        assert r.status_code == 401 and r.json()["detail"]["remaining"] == 4 - i
    assert cl.post("/auth/login", json={"login_id": "sky01", "password": "0"}).status_code == 423
    assert cl.post(f"/children/{cid}/reset-password", json={"password": "4321"}, headers=h).status_code == 200
    lc = cl.post("/auth/login", json={"login_id": "sky01", "password": "4321"}).json()
    k = {"Authorization": f"Bearer {lc['session']}"}
    assert cl.put(f"/children/{cid}/ai-consent", json={"ai_consent": True}, headers=h).status_code == 200
    assert cl.post(f"/children/{cid}/chat-access", headers=k).status_code == 200
    assert cl.post("/auth/logout", headers=k).status_code == 200
    assert cl.get("/auth/me", headers=k).status_code == 401
