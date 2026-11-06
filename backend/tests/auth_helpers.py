"""HTTP helpers for the composed app: parent id/password sign-up + login, parent-created child accounts,
child login. ``prefix`` is "/api" for app.main."""
import uuid

PARENT_PW = "parent-pw-1"
CHILD_PW = "kid1234"


def bearer(session: str) -> dict:
    return {"Authorization": "Bearer " + session}


def signup(c, login_id: str, password: str = PARENT_PW, email: str = None, prefix: str = "/api"):
    return c.post(f"{prefix}/parents/signup", json={
        "login_id": login_id, "password": password, "email": email or f"{login_id}@x.kr",
        "agree_terms": True, "agree_privacy": True, "agree_legal_guardian": True})


def login(c, login_id: str, password: str, prefix: str = "/api"):
    return c.post(f"{prefix}/auth/login", json={"login_id": login_id, "password": password})


def parent(c, login_id: str = "mom01", prefix: str = "/api", default: bool = True) -> dict:
    """Sign up + log in a parent. With ``default`` the parent session also becomes the client's default
    Authorization header (child learning routes accept the owning parent's session too)."""
    r = signup(c, login_id, prefix=prefix)
    assert r.status_code == 201, r.text
    H = bearer(login(c, login_id, PARENT_PW, prefix).json()["session"])
    if default:
        c.headers.update(H)
    return H


def new_child(c, H: dict, nickname: str, login_id: str = None, consent: bool = True, prefix: str = "/api") -> str:
    login_id = login_id or "kid-" + uuid.uuid4().hex[:10]
    r = c.post(f"{prefix}/children", headers=H, json={"login_id": login_id, "password": CHILD_PW,
                                                      "nickname": nickname, "ai_consent": consent})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def child_login(c, child_login_id: str, password: str = CHILD_PW, prefix: str = "/api") -> dict:
    r = login(c, child_login_id, password, prefix)
    assert r.status_code == 200 and r.json()["role"] == "child", r.text
    return bearer(r.json()["session"])
