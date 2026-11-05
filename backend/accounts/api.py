from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, FastAPI, Header, HTTPException
from pydantic import BaseModel

from .service import (
    AccountError, AccountService, ChatbotDisabled, ConsentRequired, Forbidden,
    IdentityNotVerified, InvalidPin, NotFound, ProfileLocked,
)

STATUS = {
    IdentityNotVerified: 401, Forbidden: 403, NotFound: 404, ConsentRequired: 400,
    ProfileLocked: 423, InvalidPin: 401, ChatbotDisabled: 403,
}


class SignupIn(BaseModel):
    verification_token: str
    email: str
    agree_terms: bool = False
    agree_privacy: bool = False
    agree_legal_guardian: bool = False


class ChildIn(BaseModel):
    nickname: str
    pin: str
    ai_consent: bool = False


class PinIn(BaseModel):
    pin: str


class ConsentIn(BaseModel):
    ai_consent: bool


class LoginIn(BaseModel):
    verification_token: str


def create_router(svc: AccountService) -> APIRouter:
    r = APIRouter()

    def guard(fn):
        try:
            return fn()
        except AccountError as e:
            detail = {"code": e.code, "message": str(e)}
            if isinstance(e, InvalidPin):
                detail["remaining"] = e.remaining
            raise HTTPException(STATUS.get(type(e), 422), detail)

    def parent_id(auth: Optional[str]) -> str:
        return guard(lambda: svc.parent_for_session((auth or "").removeprefix("Bearer ").strip()).id)

    @r.post("/parents/signup", status_code=201)
    def signup(body: SignupIn):
        p = guard(lambda: svc.signup_parent(
            body.verification_token, body.email, body.agree_terms,
            body.agree_privacy, body.agree_legal_guardian))
        return {"id": p.id, "email": p.email}

    @r.post("/parents/login")
    def login(body: LoginIn):
        return {"session": guard(lambda: svc.login_parent(body.verification_token))}

    @r.post("/children", status_code=201)
    def create_child(body: ChildIn, authorization: Optional[str] = Header(None)):
        pid = parent_id(authorization)
        c = guard(lambda: svc.create_child(pid, body.nickname, body.pin, body.ai_consent))
        return {"id": c.id, "nickname": c.nickname, "ai_consent": c.ai_consent}

    @r.post("/children/{child_id}/verify-pin")
    def verify_pin(child_id: str, body: PinIn):
        guard(lambda: svc.verify_pin(child_id, body.pin))
        return {"ok": True}

    @r.post("/children/{child_id}/reset-pin")
    def reset_pin(child_id: str, body: PinIn, authorization: Optional[str] = Header(None)):
        pid = parent_id(authorization)
        guard(lambda: svc.reset_pin(pid, child_id, body.pin))
        return {"ok": True}

    @r.put("/children/{child_id}/ai-consent")
    def ai_consent(child_id: str, body: ConsentIn, authorization: Optional[str] = Header(None)):
        pid = parent_id(authorization)
        guard(lambda: svc.set_ai_consent(pid, child_id, body.ai_consent))
        return {"ai_consent": body.ai_consent}

    @r.get("/children/{child_id}/features")
    def features(child_id: str):
        return guard(lambda: svc.features(child_id))

    @r.post("/children/{child_id}/chat-access")
    def chat_access(child_id: str):
        guard(lambda: svc.require_chatbot(child_id))
        return {"ok": True}

    return r


def create_app(svc: AccountService) -> FastAPI:
    app = FastAPI()
    app.include_router(create_router(svc))
    return app
