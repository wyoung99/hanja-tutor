from __future__ import annotations

import hmac
import os
from typing import Callable, Optional

from fastapi import APIRouter, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from billing.service import BillingError

from .service import RefundError, RefundService

ADMIN_PAGE = """<!doctype html><html lang="ko"><meta charset="utf-8"><title>환불 검토</title>
<body><h1>환불 검토 요청</h1>
<label>관리자 토큰 <input id="tok" type="password"></label> <button onclick="load()">불러오기</button>
<div id="list"></div>
<script>
const H=()=>({'X-Admin-Token':document.getElementById('tok').value,'Content-Type':'application/json'});
async function load(){
  const r=await fetch('/admin/refunds?status=review_requested',{headers:H()});
  const box=document.getElementById('list'); box.textContent='';
  if(!r.ok){box.textContent='권한이 없어요.';return;}
  for(const q of await r.json()){
    const d=document.createElement('div'); d.style.border='1px solid #999'; d.style.margin='8px'; d.style.padding='8px';
    const t=document.createElement('p'); t.textContent=q.parent_id+' · '+q.amount+'원 · '+q.reason; d.appendChild(t);
    const ul=document.createElement('ul');
    for(const u of q.usage_records){const li=document.createElement('li');li.textContent=u.at+' '+u.child_id+' '+u.label;ul.appendChild(li);}
    d.appendChild(ul);
    const note=document.createElement('input'); note.placeholder='메모'; d.appendChild(note);
    for(const [k,l] of [['approve','승인'],['reject','거절']]){
      const b=document.createElement('button'); b.textContent=l;
      b.onclick=async()=>{await fetch('/admin/refunds/'+q.id+'/'+k,{method:'POST',headers:H(),body:JSON.stringify({note:note.value})});load();};
      d.appendChild(b);}
    box.appendChild(d);}
}
</script></body></html>"""


class NoteIn(BaseModel):
    note: str = ""


def view(r) -> dict:
    return {"id": r.id, "parent_id": r.parent_id, "status": r.status, "amount": r.amount,
            "reason": r.reason, "usage_records": r.usage_records,
            "requested_at": r.requested_at.isoformat(), "operator_note": r.operator_note,
            "can_request_review": r.status == "auto_rejected"}


def create_router(svc: RefundService, parent_of_session: Callable[[str], str],
                  admin_token: Optional[str] = None) -> APIRouter:
    r = APIRouter()
    token = admin_token if admin_token is not None else os.environ.get("ADMIN_TOKEN", "")

    def pid(auth: Optional[str]) -> str:
        try:
            return parent_of_session((auth or "").removeprefix("Bearer ").strip())
        except Exception:
            raise HTTPException(401, {"code": "login_required"})

    def admin(tok: Optional[str]) -> None:
        if not token or not hmac.compare_digest(tok or "", token):
            raise HTTPException(403, {"code": "admin_only"})

    def guard(fn):
        try:
            return fn()
        except (RefundError, BillingError) as e:
            code = 404 if e.code == "not_found" else 402 if e.code == "payment_failed" else 400
            raise HTTPException(code, {"code": e.code, "message": str(e)})

    @r.post("/refunds", status_code=201)
    def request(authorization: Optional[str] = Header(None)):
        return view(guard(lambda: svc.request_refund(pid(authorization))))

    @r.get("/refunds")
    def mine(authorization: Optional[str] = Header(None)):
        return [view(x) for x in svc.list_for_parent(pid(authorization))]

    @r.post("/refunds/{rid}/review")
    def review(rid: str, authorization: Optional[str] = Header(None)):
        p = pid(authorization)
        return view(guard(lambda: svc.request_review(p, rid)))

    @r.get("/admin/refunds")
    def admin_list(status: Optional[str] = None, x_admin_token: Optional[str] = Header(None)):
        admin(x_admin_token)
        return [view(x) for x in svc.all_requests(status)]

    @r.post("/admin/refunds/{rid}/approve")
    def approve(rid: str, body: NoteIn, x_admin_token: Optional[str] = Header(None)):
        admin(x_admin_token)
        return view(guard(lambda: svc.approve(rid, body.note)))

    @r.post("/admin/refunds/{rid}/reject")
    def reject(rid: str, body: NoteIn, x_admin_token: Optional[str] = Header(None)):
        admin(x_admin_token)
        return view(guard(lambda: svc.reject(rid, body.note)))

    @r.get("/admin/refunds-ui", response_class=HTMLResponse)
    def ui():
        return ADMIN_PAGE

    return r


def create_app(svc: RefundService, parent_of_session: Callable[[str], str],
               admin_token: Optional[str] = None) -> FastAPI:
    app = FastAPI()
    app.include_router(create_router(svc, parent_of_session, admin_token))
    return app
