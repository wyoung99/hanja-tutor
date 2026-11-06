"use client";
import { useEffect, useState } from "react";
import { api, setAuth } from "../../../lib/api";

/** Toss Payments redirect target after card registration (requestBillingAuth). */
export default function BillingSuccess() {
  const [msg, setMsg] = useState("결제 확인 중…");
  useEffect(() => {
    const q = new URLSearchParams(location.search);
    try { setAuth(JSON.parse(sessionStorage.getItem("hanja.session") ?? "null")); } catch { /* not logged in */ }
    api("/billing/subscribe", { body: { plan_id: sessionStorage.getItem("hanja.plan") ?? "monthly", auth_key: q.get("authKey") ?? "" } })
      .then(() => setMsg("구독이 시작됐어요. 앱으로 돌아가세요."))
      .catch(() => setMsg("결제에 실패했어요."));
  }, []);
  return <main><p>{msg}</p><a href="/">돌아가기</a></main>;
}
