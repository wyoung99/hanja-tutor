"use client";
import { useState } from "react";
import { api } from "../../lib/api";
import { Alert, Badge, Button, Card, CardTitle, inputClass, Logo, Muted } from "../../components/ui";

export default function Admin() {
  const [tok, setTok] = useState("");
  const [rows, setRows] = useState<any[]>([]);
  const [err, setErr] = useState("");
  const load = () => api("/admin/refunds?status=review_requested", { admin: tok }).then(setRows).then(() => setErr("")).catch(() => setErr("권한이 없어요."));
  const act = (id: string, k: "approve" | "reject") => api(`/admin/refunds/${id}/${k}`, { admin: tok, body: { note: "" } }).then(load);
  return (
    <main className="mx-auto max-w-4xl space-y-6 px-4 py-8">
      <div className="flex items-center gap-3">
        <Logo size="sm" />
        <h1 className="text-2xl font-extrabold tracking-tight">환불 검토</h1>
        <Badge tone="info">운영자</Badge>
      </div>
      <Card>
        <div className="flex flex-col gap-2 sm:flex-row">
          <input className={inputClass} type="password" aria-label="관리자 토큰" placeholder="관리자 토큰" value={tok} onChange={(e) => setTok(e.target.value)} />
          <Button className="shrink-0" onClick={load}>불러오기</Button>
        </div>
        {err && <Alert tone="danger" role="alert" className="mt-3">{err}</Alert>}
      </Card>
      {rows.map((r) => (
        <Card key={r.id}>
          <CardTitle as="h3" aside={<Badge tone="warning">검토 요청</Badge>}>{r.parent_id} · {r.amount}원</CardTitle>
          <Muted className="-mt-2 mb-3">{r.reason}</Muted>
          <ul className="mb-4 space-y-1 rounded-xl bg-soft/60 p-3 text-sm">{r.usage_records.map((u: any, i: number) => <li key={i}>{u.at} {u.child_id} {u.label}</li>)}</ul>
          <div className="flex gap-2">
            <Button variant="success" onClick={() => act(r.id, "approve")}>승인</Button>
            <Button variant="secondary" onClick={() => act(r.id, "reject")}>거절</Button>
          </div>
        </Card>
      ))}
    </main>
  );
}
