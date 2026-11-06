from __future__ import annotations

from typing import Callable, Optional

from fastapi import APIRouter, FastAPI, Header, HTTPException
from pydantic import BaseModel

from .service import (
    CHILD, PARENT, AccountDormant, AccountError, AccountService, AlreadyRegistered, ChatbotDisabled,
    ChildProfile, ConsentRequired, Forbidden, InvalidCredentials, LoginRequired, LoginResult, NotFound,
    ParentOnly, ProfileLocked,
)

STATUS = {
    LoginRequired: 401, ParentOnly: 403, Forbidden: 403, NotFound: 404, ConsentRequired: 400,
    AlreadyRegistered: 409, ProfileLocked: 423, InvalidCredentials: 401, AccountDormant: 403,
    ChatbotDisabled: 403,
}


def http_error(e: AccountError) -> HTTPException:
    detail = {"code": e.code, "message": str(e)}
    if isinstance(e, InvalidCredentials) and e.remaining is not None:
        detail["remaining"] = e.remaining
    return HTTPException(STATUS.get(type(e), 422), detail)


def bearer(auth: Optional[str]) -> str:
    return (auth or "").removeprefix("Bearer ").strip()


class SignupIn(BaseModel):
    login_id: str
    password: str
    email: str
    agree_terms: bool = False
    agree_privacy: bool = False
    agree_legal_guardian: bool = False


class LoginIn(BaseModel):
    login_id: str
    password: str


class ChildIn(BaseModel):
    login_id: str
    password: str
    nickname: str
    ai_consent: bool = False


class PasswordIn(BaseModel):
    password: str


class ConsentIn(BaseModel):
    ai_consent: bool


def login_json(r: LoginResult) -> dict:
    return {"session": r.session, "role": r.role, "account_id": r.account_id, "parent_id": r.parent_id,
            "nickname": r.nickname, "restored_from_dormancy": r.restored_from_dormancy}


def child_access(svc: AccountService, auth: Optional[str], child_id: str) -> ChildProfile:
    """Child (learning) routes: the child's own session, or the session of the parent who owns the child.
    Anything else is 401 (no / unknown session) or 404 (someone else's child)."""
    who = svc.principal(bearer(auth))
    if who is None:
        raise HTTPException(401, {"code": "login_required"})
    c = svc.children.get(child_id)
    role, aid = who
    if c is None or (role == CHILD and aid != child_id) or (role == PARENT and c.parent_id != aid):
        raise HTTPException(404, {"code": "not_found"})
    return c


def create_router(svc: AccountService, login: Optional[Callable[[str, str], LoginResult]] = None) -> APIRouter:
    """`login`: optional (login_id, password) -> LoginResult override, e.g. LifecycleService.login which also
    restores a dormant account's separately stored data."""
    r = APIRouter()
    do_login = login or svc.login

    def guard(fn):
        try:
            return fn()
        except AccountError as e:
            raise http_error(e)

    def parent_id(auth: Optional[str]) -> str:
        return guard(lambda: svc.parent_for_session(bearer(auth)).id)

    @r.post("/parents/signup", status_code=201)
    def signup(body: SignupIn):
        p = guard(lambda: svc.signup_parent(
            body.login_id, body.password, body.email, body.agree_terms,
            body.agree_privacy, body.agree_legal_guardian))
        return {"id": p.id, "login_id": p.login_id, "email": p.email}

    @r.post("/auth/login")
    def do_login_route(body: LoginIn):
        return login_json(guard(lambda: do_login(body.login_id, body.password)))

    @r.post("/auth/logout")
    def logout(authorization: Optional[str] = Header(None)):
        svc.logout(bearer(authorization))
        return {"ok": True}

    @r.get("/auth/me")
    def me(authorization: Optional[str] = Header(None)):
        who = svc.principal(bearer(authorization))
        if who is None:
            raise HTTPException(401, {"code": "login_required"})
        role, aid = who
        if role == PARENT:
            p = svc.parents[aid]
            return {"role": PARENT, "account_id": aid, "parent_id": aid, "login_id": p.login_id}
        c = svc.children[aid]
        return {"role": CHILD, "account_id": aid, "parent_id": c.parent_id, "login_id": c.login_id,
                "nickname": c.nickname, "ai_consent": c.ai_consent, "target": list(c.extra.get("target") or [])}

    @r.post("/children", status_code=201)
    def create_child(body: ChildIn, authorization: Optional[str] = Header(None)):
        pid = parent_id(authorization)
        c = guard(lambda: svc.create_child(pid, body.nickname, body.password, body.ai_consent,
                                           login_id=body.login_id))
        return {"id": c.id, "login_id": c.login_id, "nickname": c.nickname, "ai_consent": c.ai_consent}

    @r.post("/children/{child_id}/reset-password")
    def reset_password(child_id: str, body: PasswordIn, authorization: Optional[str] = Header(None)):
        pid = parent_id(authorization)
        guard(lambda: svc.reset_child_password(pid, child_id, body.password))
        return {"ok": True}

    @r.put("/children/{child_id}/ai-consent")
    def ai_consent(child_id: str, body: ConsentIn, authorization: Optional[str] = Header(None)):
        pid = parent_id(authorization)
        guard(lambda: svc.set_ai_consent(pid, child_id, body.ai_consent))
        return {"ai_consent": body.ai_consent}

    @r.get("/children/{child_id}/features")
    def features(child_id: str, authorization: Optional[str] = Header(None)):
        child_access(svc, authorization, child_id)
        return guard(lambda: svc.features(child_id))

    @r.post("/children/{child_id}/chat-access")
    def chat_access(child_id: str, authorization: Optional[str] = Header(None)):
        child_access(svc, authorization, child_id)
        guard(lambda: svc.require_chatbot(child_id))
        return {"ok": True}

    return r


def create_app(svc: AccountService) -> FastAPI:
    app = FastAPI()
    app.include_router(create_router(svc))
    return app
