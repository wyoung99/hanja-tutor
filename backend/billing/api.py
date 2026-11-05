from __future__ import annotations

from typing import Callable, Optional

from fastapi import APIRouter, FastAPI, Header, HTTPException
from pydantic import BaseModel

from .service import BillingError, BillingService


class SubscribeIn(BaseModel):
    plan_id: str
    auth_key: str  # Toss requestBillingAuth success parameter
    child_ids: Optional[list] = None


def create_router(svc: BillingService, parent_of_session: Callable[[str], str]) -> APIRouter:
    r = APIRouter(prefix="/billing")

    def pid(auth: Optional[str]) -> str:
        try:
            return parent_of_session((auth or "").removeprefix("Bearer ").strip())
        except Exception:
            raise HTTPException(401, {"code": "login_required"})

    def guard(fn):
        try:
            return fn()
        except BillingError as e:
            raise HTTPException(402 if e.code == "payment_failed" else 400,
                                {"code": e.code, "message": str(e)})

    def view(s):
        return {"plan_id": s.plan_id, "status": s.status, "period_end": s.period_end.isoformat(),
                "child_ids": s.child_ids}

    @r.get("/plans")
    def plans():
        return svc.plans()

    @r.post("/subscribe", status_code=201)
    def subscribe(body: SubscribeIn, authorization: Optional[str] = Header(None)):
        p = pid(authorization)
        return view(guard(lambda: svc.subscribe(p, body.plan_id, body.auth_key, body.child_ids)))

    @r.post("/cancel")
    def cancel(authorization: Optional[str] = Header(None)):
        p = pid(authorization)
        return view(guard(lambda: svc.cancel(p)))

    @r.get("/subscription")
    def get(authorization: Optional[str] = Header(None)):
        s = svc.get(pid(authorization))
        return view(s) if s else {"status": "free"}

    return r


def create_app(svc: BillingService, parent_of_session: Callable[[str], str]) -> FastAPI:
    app = FastAPI()
    app.include_router(create_router(svc, parent_of_session))
    return app
