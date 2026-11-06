"use client";
import { useEffect, useState } from "react";
import {
  api, ApiError, CHILD_MENUS, loadAuth, loginIdError, LOGOUT_EVENT, PARENT_MENUS, passLine, passwordError, session,
  setAuth, won, type Auth,
} from "../lib/api";
import { gradeLabel } from "../lib/strokes";
import { Chat, Exam, reading, ScreenGuard, Tracing, type Card, type Child } from "../components/ChildPanels";
import { Account, Alerts, ChildCard, Reports } from "../components/ParentPanels";
import { LoginForm } from "../components/Auth";

type ExamInfo = {
  status: "available" | "format_pending" | "unsupported"; message: string | null;
  questions?: number; minutes?: number; total_score?: number; pass_score?: number; pass_questions?: number | null;
  points_per_question?: number | null; type_mix?: string; notice?: string | null; all_multiple_choice?: boolean;
};
type Catalog = { body: string; name: string; grades: string[]; exam: Record<string, ExamInfo> }[];

/** Landing page = login. After login the server-decided role picks the screen: parents get management menus
 * only, children learning menus only (the server refuses child sessions on parent APIs as well). */
export default function Home() {
  const [ready, setReady] = useState(false);
  const [auth, setAuthState] = useState<Auth | null>(null);
  useEffect(() => {
    setAuthState(loadAuth());
    setReady(true);
    const onLogout = () => setAuthState(null);
    window.addEventListener(LOGOUT_EVENT, onLogout);
    return () => window.removeEventListener(LOGOUT_EVENT, onLogout);
  }, []);
  async function logout() {
    await api("/auth/logout", { body: {} }).catch(() => undefined);
    setAuth(null);
    setAuthState(null);
  }
  return (
    <main>
      <h1>한자 튜터</h1>
      {ready && !auth && <LoginForm onLoggedIn={setAuthState} />}
      {auth?.role === "parent" && <Parent onLogout={logout} onDeleted={() => { setAuth(null); setAuthState(null); }} />}
      {auth?.role === "child" && <ChildApp childId={auth.accountId} onLogout={logout} />}
    </main>
  );
}

// ---------------------------------------------------------------- parent (management menus only)
function Parent({ onLogout, onDeleted }: { onLogout: () => void; onDeleted: () => void }) {
  const [kids, setKids] = useState<Child[]>([]);
  const [catalog, setCatalog] = useState<Catalog>([]);
  const [nick, setNick] = useState("");
  const [kidId, setKidId] = useState("");
  const [kidPw, setKidPw] = useState("");
  const [ai, setAi] = useState(false);
  const [body, setBody] = useState("");
  const [grade, setGrade] = useState("");
  // a tapped push notification opens /?alerts=1 (new window) or messages an open window (sw.js)
  const [tab, setTab] = useState<(typeof PARENT_MENUS)[number]["key"]>(
    () => (typeof location !== "undefined" && new URLSearchParams(location.search).has("alerts") ? "alerts" : "kids"));
  useEffect(() => {
    const sw = typeof navigator !== "undefined" ? navigator.serviceWorker : undefined;
    const onMsg = (e: MessageEvent) => { if (e.data?.type === "open-alerts") setTab("alerts"); };
    sw?.addEventListener("message", onMsg);
    return () => sw?.removeEventListener("message", onMsg);
  }, []);
  const [err, setErr] = useState("");
  const [created, setCreated] = useState("");
  const reload = async () => setKids(await api("/children").catch(() => []));
  useEffect(() => {
    reload();
    api("/catalog").then((c) => {
      setCatalog(c);
      setBody(c[0]?.body ?? "");
      setGrade(c[0]?.grades[0] ?? "");
    });
  }, []);

  async function addChild() {
    setErr(""); setCreated("");
    const local = loginIdError(kidId) || passwordError(kidPw, "child") || (nick.trim() ? "" : "닉네임을 적어 주세요.");
    if (local) return setErr(local);
    try {
      const c = await api("/children", { body: { login_id: kidId.trim(), password: kidPw, nickname: nick, ai_consent: ai } });
      await api(`/children/${c.id}/target`, { method: "PUT", body: { body, grade } });
      setCreated(`${c.nickname} 계정을 만들었어요. 아이 기기에서 아이디 ${c.login_id}(으)로 로그인하면 돼요.`);
      setNick(""); setKidId(""); setKidPw("");
      await reload();
    } catch (e) {
      setErr(e instanceof ApiError ? (e.detail?.code === "login_id_taken" ? "이미 쓰고 있는 아이디예요." : (e.detail?.message ?? e.detail?.code ?? "error")) : "error");
    }
  }
  const entry = catalog.find((c) => c.body === body);
  const grades = entry?.grades ?? [];
  const info = entry?.exam[grade];
  const pendingNote = info?.status === "format_pending";
  return (
    <>
      <nav data-testid="parent-menu">
        {PARENT_MENUS.map((m) => (
          <button key={m.key} className="secondary" data-testid={`tab-${m.key}`} onClick={() => { setTab(m.key); if (m.key === "kids") reload(); }}>{m.label}</button>
        ))}
        <button className="secondary" data-testid="logout" onClick={onLogout}>로그아웃</button>
      </nav>
      {tab === "kids" && (
        <>
          <section className="card" aria-label="자녀 계정 만들기">
            <h2>자녀 계정 만들기</h2>
            <p>아이는 스스로 가입할 수 없어요. 여기서 정한 아이디·비밀번호로 아이 기기에서 로그인해요.</p>
            <label>닉네임 (실명 대신)<input data-testid="nick" value={nick} onChange={(e) => setNick(e.target.value)} /></label>
            <label>자녀 아이디 (영문·숫자 4~20자)<input data-testid="child-login-id" autoComplete="off" value={kidId} onChange={(e) => setKidId(e.target.value)} /></label>
            <label>자녀 비밀번호 (4자 이상)<input data-testid="child-password" type="password" autoComplete="new-password" value={kidPw} onChange={(e) => setKidPw(e.target.value)} /></label>
            <label>시험 기관
              <select data-testid="body" value={body} onChange={(e) => { setBody(e.target.value); setGrade(catalog.find((c) => c.body === e.target.value)?.grades[0] ?? ""); }}>
                {catalog.map((c) => <option key={c.body} value={c.body}>{c.name}</option>)}
              </select>
            </label>
            <label>급수
              <select data-testid="grade" value={grade} onChange={(e) => setGrade(e.target.value)}>
                {grades.map((g) => (
                  <option key={g} value={g}>{gradeLabel(g)}{entry?.exam[g]?.status === "format_pending" ? " (모의고사 형식 준비 중)" : ""}</option>
                ))}
              </select>
            </label>
            {info?.status === "available" && (
              <p data-testid="grade-format">
                모의고사: {info.questions}문항 · {info.minutes}분 · 합격 {passLine({ ...info, pass_score: info.pass_score ?? 0 })}
                {info.all_multiple_choice ? " · 모두 객관식" : ""}
                {info.notice && <><br /><small data-testid="grade-notice">{info.notice}</small></>}
              </p>
            )}
            {pendingNote && <p data-testid="grade-note">이 급수는 공식 모의고사 형식을 확인하는 중이라 모의고사는 아직 볼 수 없어요. 오늘의 한자·복습·따라쓰기는 모두 할 수 있어요.</p>}
            <label className="check"><input type="checkbox" data-testid="ai-consent" checked={ai} onChange={(e) => setAi(e.target.checked)} />대화·학습 기록의 AI 처리에 동의합니다 (미동의 시 챗봇만 꺼져요)</label>
            <button data-testid="add-child" onClick={addChild}>자녀 계정 만들기</button>
            {err && <p className="err" role="alert" data-testid="add-child-error">{err}</p>}
            {created && <p data-testid="child-created">{created}</p>}
          </section>
          {kids.map((k) => (
            <ChildCard key={k.id} k={k} bodyName={catalog.find((c) => c.body === k.target[0])?.name ?? k.target[0]}
              reload={reload} />
          ))}
        </>
      )}
      {tab === "report" && <Reports kids={kids} />}
      {tab === "alerts" && <Alerts />}
      {tab === "billing" && <Billing />}
      {tab === "account" && <Account onDeleted={onDeleted} />}
    </>
  );
}

// ---------------------------------------------------------------- billing (parent only; AC14/AC15 implement-only)
function Billing() {
  const [plans, setPlans] = useState<any[]>([]);
  const [sub, setSub] = useState<any>(null);
  const [refund, setRefund] = useState<any>(null);
  const [err, setErr] = useState("");
  const reload = () => api("/billing/subscription").then(setSub);
  useEffect(() => {
    api("/billing/plans").then(setPlans);
    reload();
  }, []);

  async function pay(planId: string) {
    setErr("");
    const clientKey = process.env.NEXT_PUBLIC_TOSS_CLIENT_KEY;
    try {
      if (clientKey) {
        // Toss Payments test/live mode: redirect to card registration, /billing/success finishes it.
        await loadScript("https://js.tosspayments.com/v1/payment");
        const toss = (window as any).TossPayments(clientKey);
        sessionStorage.setItem("hanja.session", JSON.stringify(session.auth));
        sessionStorage.setItem("hanja.plan", planId);
        await toss.requestBillingAuth("카드", {
          customerKey: "cust-" + (session.token ?? "").slice(0, 16),
          successUrl: `${location.origin}/billing/success`,
          failUrl: `${location.origin}/billing/fail`,
        });
        return;
      }
      await api("/billing/subscribe", { body: { plan_id: planId, auth_key: "fake-auth-key" } });
      await reload();
    } catch (e) {
      setErr(e instanceof ApiError ? (e.detail?.code ?? "error") : "error");
    }
  }
  async function cancel() { await api("/billing/cancel", { body: {} }); await reload(); }
  async function requestRefund() {
    setErr("");
    try { setRefund(await api("/refunds", { body: {} })); await reload(); }
    catch (e) { setErr(e instanceof ApiError ? (e.detail?.code ?? "error") : "error"); }
  }
  async function requestReview() { setRefund(await api(`/refunds/${refund.id}/review`, { body: {} })); }

  const subscribed = sub && sub.status !== "free" && sub.status !== "ended";
  return (
    <section className="card" aria-label="구독">
      <h2>구독</h2>
      <p>무료 플랜에서도 오늘의 한자·복습·따라쓰기·기본 리포트·안전 기능은 항상 무제한이에요.</p>
      {!subscribed && plans.map((p) => (
        <div key={p.id}>
          <span data-testid={`price-${p.id}`}>{p.name} {won(p.price_krw)}</span>
          <button data-testid={`pay-${p.id}`} onClick={() => pay(p.id)}>결제하기</button>
        </div>
      ))}
      {subscribed && (
        <>
          <p data-testid="sub-status">구독 상태: {sub.status} ({sub.plan_id}) · {sub.period_end?.slice(0, 10)}까지</p>
          <button className="secondary" onClick={cancel}>해지</button>
          <button data-testid="refund" onClick={requestRefund}>7일 환불 요청</button>
        </>
      )}
      {refund && (
        <div data-testid="refund-result">
          <p>상태: <span data-testid="refund-status">{refund.status}</span></p>
          {refund.status === "auto_rejected" && (
            <>
              <p data-testid="refund-reason">거절 사유: {refund.reason}</p>
              <ul>{refund.usage_records.map((u: any, i: number) => <li key={i}>{u.label}</li>)}</ul>
              {refund.can_request_review && <button data-testid="refund-review" onClick={requestReview}>운영자 검토 요청</button>}
            </>
          )}
        </div>
      )}
      {sub && sub.status === "free" && !subscribed && refund?.status === "auto_approved" && <p>환불이 완료되어 구독이 종료됐어요.</p>}
      {err && <p className="err" role="alert">{err}</p>}
    </section>
  );
}

function loadScript(src: string) {
  return new Promise<void>((resolve, reject) => {
    if (document.querySelector(`script[src="${src}"]`)) return resolve();
    const s = document.createElement("script");
    s.src = src; s.onload = () => resolve(); s.onerror = () => reject(new Error("script"));
    document.head.appendChild(s);
  });
}

// ---------------------------------------------------------------- child (learning menus only)
function ChildApp({ childId, onLogout }: { childId: string; onLogout: () => void }) {
  const [child, setChild] = useState<Child | null>(null);
  const [tab, setTab] = useState<(typeof CHILD_MENUS)[number]["key"]>("today");
  useEffect(() => {
    api("/auth/me").then((m) => setChild({ id: m.account_id, nickname: m.nickname, ai_consent: m.ai_consent, target: m.target }))
      .catch(() => undefined);
  }, [childId]);
  if (!child) return <p>불러오는 중…</p>;
  return (
    <ScreenGuard child={child}>
      <p data-testid="child-hello">{child.nickname}, 안녕! 오늘도 한자 공부해요.</p>
      <nav data-testid="child-menu">
        {CHILD_MENUS.map((m) => (
          <button key={m.key} className="secondary" data-testid={`tab-${m.key}`} onClick={() => setTab(m.key)}>{m.label}</button>
        ))}
        <button className="secondary" data-testid="logout" onClick={onLogout}>로그아웃</button>
      </nav>
      {(tab === "today" || tab === "review" || tab === "tracing") && <Learn key={tab} child={child} mode={tab} />}
      {tab === "exam" && <Exam child={child} />}
      {tab === "chat" && <Chat child={child} />}
    </ScreenGuard>
  );
}

/** 오늘의 한자 (new chars), 복습 (due reviews) and 따라쓰기 (every char of today's session). */
function Learn({ child, mode }: { child: Child; mode: "today" | "review" | "tracing" }) {
  const [data, setData] = useState<{ new: Card[]; reviews: Card[] } | null>(null);
  const [tracing, setTracing] = useState<Card | null>(null);
  const [done, setDone] = useState<string[]>([]);
  const [msg, setMsg] = useState("");
  const load = () => api(`/children/${child.id}/today`).then(setData)
    .catch((e) => setMsg(e instanceof ApiError && e.status === 423 ? "지금은 쉬는 시간이에요."
      : e instanceof ApiError && e.status === 409 ? "부모님이 아직 급수를 정하지 않았어요." : "불러오지 못했어요."));
  useEffect(() => { load(); }, []);
  if (tracing)
    return <Tracing child={child} card={tracing} onDone={async (ok) => {
      const isNew = data?.new.some((c) => c.char === tracing.char);
      if (ok && isNew) await api(`/children/${child.id}/today/${tracing.char}/complete`, { body: {} }).catch(() => undefined);
      if (ok) setDone((d) => [...d, tracing.char]);
      setTracing(null);
    }} />;
  if (!data) return <p>{msg || "불러오는 중…"}</p>;
  const traceButton = (c: Card) => done.includes(c.char) ? <span data-testid={`done-${c.char}`}> ✅</span>
    : c.tracing === "unavailable" ? <small> (획순 데이터가 없어 따라쓰기는 쉬어요)</small>
    : <button data-testid={`trace-${c.char}`} onClick={() => setTracing(c)}>따라 쓰기</button>;
  if (mode === "today")
    return (
      <section className="card" aria-label="오늘의 한자">
        <h2>오늘의 한자</h2>
        {data.new.map((c) => (
          <div key={c.char} data-testid="today-card">
            <span style={{ fontSize: 40 }}>{c.char}</span> <span data-testid="reading">{reading(c)}</span>
            {traceButton(c)}
          </div>
        ))}
      </section>
    );
  if (mode === "review")
    return (
      <section className="card" aria-label="복습">
        <h2>복습</h2>
        {data.reviews.length === 0 && <p data-testid="no-review">오늘 복습할 한자가 없어요.</p>}
        {data.reviews.map((c) => (
          <div key={c.char} data-testid="review-card">{c.char} {reading(c)}
            <button onClick={async () => { await api(`/children/${child.id}/reviews/${c.char}`, { body: { correct: true } }); load(); }}>알아요</button>
            <button className="secondary" onClick={async () => { await api(`/children/${child.id}/reviews/${c.char}`, { body: { correct: false } }); load(); }}>몰라요</button>
          </div>
        ))}
      </section>
    );
  const all = [...data.new, ...data.reviews.filter((r) => !data.new.some((n) => n.char === r.char))];
  return (
    <section className="card" aria-label="따라쓰기">
      <h2>따라쓰기</h2>
      {all.map((c) => (
        <div key={c.char} data-testid="tracing-card"><span style={{ fontSize: 32 }}>{c.char}</span> {reading(c)} {traceButton(c)}</div>
      ))}
    </section>
  );
}
