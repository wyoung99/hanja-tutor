"use client";
import { useState } from "react";
import { api } from "../../lib/api";

export default function Admin() {
  const [tok, setTok] = useState("");
  const [rows, setRows] = useState<any[]>([]);
  const [err, setErr] = useState("");
  const load = () => api("/admin/refunds?status=review_requested", { admin: tok }).then(setRows).then(() => setErr("")).catch(() => setErr("권한이 없어요."));
  const act = (id: string, k: "approve" | "reject") => api(`/admin/refunds/${id}/${k}`, { admin: tok, body: { note: "" } }).then(load);
  return (
    <main>
      <h1>환불 검토</h1>
      <input type="password" placeholder="관리자 토큰" value={tok} onChange={(e) => setTok(e.target.value)} />
      <button onClick={load}>불러오기</button>
      {err && <p role="alert">{err}</p>}
      {rows.map((r) => (
        <div className="card" key={r.id}>
          <p>{r.parent_id} · {r.amount}원 · {r.reason}</p>
          <ul>{r.usage_records.map((u: any, i: number) => <li key={i}>{u.at} {u.child_id} {u.label}</li>)}</ul>
          <button onClick={() => act(r.id, "approve")}>승인</button>
          <button className="secondary" onClick={() => act(r.id, "reject")}>거절</button>
        </div>
      ))}
    </main>
  );
}
