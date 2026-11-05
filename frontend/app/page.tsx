"use client";
import { useEffect, useState } from "react";
import { api, ApiError, passLine, session, won } from "../lib/api";
import { gradeLabel } from "../lib/strokes";
import { Chat, Exam, reading, ScreenGuard, Tracing, type Card, type Child } from "../components/ChildPanels";
import { Account, Alerts, ChildCard, Reports } from "../components/ParentPanels";

type ExamInfo = {
  status: "available" | "format_pending" | "unsupported"; message: string | null;
  questions?: number; minutes?: number; total_score?: number; pass_score?: number; pass_questions?: number | null;
  points_per_question?: number | null; type_mix?: string; notice?: string | null; all_multiple_choice?: boolean;
};
type Catalog = { body: string; name: string; grades: string[]; exam: Record<string, ExamInfo> }[];
type View = "auth" | "parent" | "child";

export default function Home() {
  const [view, setView] = useState<View>("auth");
  const [child, setChild] = useState<Child | null>(null);
  return (
    <main>
      <h1>한자 튜터</h1>
      {view === "auth" && <Auth onDone={() => setView("parent")} />}
      {view === "parent" && (
        <Parent
          onEnter={(c) => {
            setChild(c);
            setView("child");
          }}
          onLoggedOut={() => { session.token = null; setView("auth"); }}
        />
      )}
      {view === "child" && child && <ChildApp child={child} onExit={() => setView("parent")} />}
    </main>
  );
}

// ---------------------------------------------------------------- auth
function Auth({ onDone }: { onDone: () => void }) {
  const [email, setEmail] = useState("");
  const [terms, setTerms] = useState(false);
  const [priv, setPriv] = useState(false);
  const [guardian, setGuardian] = useState(false);
  const [err, setErr] = useState("");
  const verify = async () => (await api("/dev/verify", { body: { ci: "ci-" + email } })).verification_token as string;

  async function signup() {
    setErr("");
    try {
      await api("/parents/signup", {
        body: { verification_token: await verify(), email, agree_terms: terms, agree_privacy: priv, agree_legal_guardian: guardian },
      });
      await login();
    } catch (e) {
      setErr(e instanceof ApiError ? (e.detail?.code ?? "error") : "error");
    }
  }
  async function login() {
    const r = await api("/parents/login", { body: { verification_token: await verify() } });
    session.token = r.session;
    onDone();
  }
  return (
    <section className="card" aria-label="가입">
      <h2>학부모 가입 (휴대폰 본인인증)</h2>
      <label>이메일 (결제 실패 알림용)<input data-testid="email" value={email} onChange={(e) => setEmail(e.target.value)} /></label>
      <label className="check"><input type="checkbox" data-testid="agree-terms" checked={terms} onChange={(e) => setTerms(e.target.checked)} />이용약관 동의</label>
      <label className="check"><input type="checkbox" data-testid="agree-privacy" checked={priv} onChange={(e) => setPriv(e.target.checked)} />개인정보 수집·이용 동의</label>
      <label className="check"><input type="checkbox" data-testid="agree-guardian" checked={guardian} onChange={(e) => setGuardian(e.target.checked)} />저는 법정대리인입니다</label>
      <button data-testid="signup" onClick={signup}>본인인증하고 가입</button>
      <button className="secondary" data-testid="login" onClick={() => login().catch(() => setErr("login_failed"))}>로그인</button>
      {err && <p className="err" role="alert">{err}</p>}
    </section>
  );
}

// ---------------------------------------------------------------- parent
function Parent({ onEnter, onLoggedOut }: { onEnter: (c: Child) => void; onLoggedOut: () => void }) {
  const [kids, setKids] = useState<Child[]>([]);
  const [catalog, setCatalog] = useState<Catalog>([]);
  const [nick, setNick] = useState("");
  const [pin, setPin] = useState("");
  const [ai, setAi] = useState(false);
  const [body, setBody] = useState("");
  const [grade, setGrade] = useState("");
  // a tapped push notification opens /?alerts=1 (new window) or messages an open window (sw.js)
  const [tab, setTab] = useState<"kids" | "report" | "alerts" | "billing" | "account">(
    () => (typeof location !== "undefined" && new URLSearchParams(location.search).has("alerts") ? "alerts" : "kids"));
  useEffect(() => {
    const sw = typeof navigator !== "undefined" ? navigator.serviceWorker : undefined;
    const onMsg = (e: MessageEvent) => { if (e.data?.type === "open-alerts") setTab("alerts"); };
    sw?.addEventListener("message", onMsg);
    return () => sw?.removeEventListener("message", onMsg);
  }, []);
  const [err, setErr] = useState("");
  const reload = async () => setKids(await api("/children", { auth: true }));
  useEffect(() => {
    reload();
    api("/catalog").then((c) => {
      setCatalog(c);
      setBody(c[0]?.body ?? "");
      setGrade(c[0]?.grades[0] ?? "");
    });
  }, []);

  async function addChild() {
    setErr("");
    try {
      const c = await api("/children", { auth: true, body: { nickname: nick, pin, ai_consent: ai } });
      await api(`/children/${c.id}/target`, { auth: true, method: "PUT", body: { body, grade } });
      setNick(""); setPin("");
      await reload();
    } catch (e) {
      setErr(e instanceof ApiError ? (e.detail?.code ?? "error") : "error");
    }
  }
  const entry = catalog.find((c) => c.body === body);
  const grades = entry?.grades ?? [];
  const info = entry?.exam[grade];
  const pendingNote = info?.status === "format_pending";
  return (
    <>
      <nav>
        <button className="secondary" data-testid="tab-kids" onClick={() => { setTab("kids"); reload(); }}>자녀</button>
        <button className="secondary" data-testid="tab-report" onClick={() => setTab("report")}>리포트·대화</button>
        <button className="secondary" data-testid="tab-alerts" onClick={() => setTab("alerts")}>알림</button>
        <button className="secondary" data-testid="tab-billing" onClick={() => setTab("billing")}>구독</button>
        <button className="secondary" data-testid="tab-account" onClick={() => setTab("account")}>계정</button>
      </nav>
      {tab === "kids" && (
        <>
          <section className="card" aria-label="자녀 프로필 만들기">
            <h2>자녀 프로필 만들기</h2>
            <label>닉네임<input data-testid="nick" value={nick} onChange={(e) => setNick(e.target.value)} /></label>
            <label>PIN (숫자 4자리)<input data-testid="pin" type="password" inputMode="numeric" value={pin} onChange={(e) => setPin(e.target.value)} /></label>
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
            <button data-testid="add-child" onClick={addChild}>프로필 만들기</button>
            {err && <p className="err" role="alert">{err}</p>}
          </section>
          {kids.map((k) => (
            <ChildCard key={k.id} k={k} bodyName={catalog.find((c) => c.body === k.target[0])?.name ?? k.target[0]}
              onEnter={() => onEnter(k)} reload={reload} />
          ))}
        </>
      )}
      {tab === "report" && <Reports kids={kids} />}
      {tab === "alerts" && <Alerts />}
      {tab === "billing" && <Billing />}
      {tab === "account" && <Account onDeleted={onLoggedOut} />}
    </>
  );
}

// ---------------------------------------------------------------- billing (parent only; AC14/AC15 implement-only)
function Billing() {
  const [plans, setPlans] = useState<any[]>([]);
  const [sub, setSub] = useState<any>(null);
  const [refund, setRefund] = useState<any>(null);
  const [err, setErr] = useState("");
  const reload = () => api("/billing/subscription", { auth: true }).then(setSub);
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
        sessionStorage.setItem("hanja.session", session.token ?? "");
        sessionStorage.setItem("hanja.plan", planId);
        await toss.requestBillingAuth("카드", {
          customerKey: "cust-" + (session.token ?? "").slice(0, 16),
          successUrl: `${location.origin}/billing/success`,
          failUrl: `${location.origin}/billing/fail`,
        });
        return;
      }
      await api("/billing/subscribe", { auth: true, body: { plan_id: planId, auth_key: "fake-auth-key" } });
      await reload();
    } catch (e) {
      setErr(e instanceof ApiError ? (e.detail?.code ?? "error") : "error");
    }
  }
  async function cancel() { await api("/billing/cancel", { auth: true, body: {} }); await reload(); }
  async function requestRefund() {
    setErr("");
    try { setRefund(await api("/refunds", { auth: true, body: {} })); await reload(); }
    catch (e) { setErr(e instanceof ApiError ? (e.detail?.code ?? "error") : "error"); }
  }
  async function requestReview() { setRefund(await api(`/refunds/${refund.id}/review`, { auth: true, body: {} })); }

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

// ---------------------------------------------------------------- child
function ChildApp({ child, onExit }: { child: Child; onExit: () => void }) {
  const [unlocked, setUnlocked] = useState(false);
  const [pin, setPin] = useState("");
  const [err, setErr] = useState("");
  const [tab, setTab] = useState<"today" | "exam" | "chat">("today");
  async function unlock() {
    try { await api(`/children/${child.id}/verify-pin`, { body: { pin } }); setUnlocked(true); setErr(""); }
    catch (e) {
      if (e instanceof ApiError && e.status === 423) setErr("잠겼어요. 부모님께 PIN을 다시 정해 달라고 하세요.");
      else setErr(`PIN이 달라요.${e instanceof ApiError && e.detail?.remaining != null ? ` (${e.detail.remaining}번 남았어요)` : ""}`);
    }
  }
  if (!unlocked)
    return (
      <section className="card">
        <h2>{child.nickname}, PIN을 입력해요</h2>
        <input data-testid="child-pin" type="password" inputMode="numeric" value={pin} onChange={(e) => setPin(e.target.value)} />
        <button data-testid="child-unlock" onClick={unlock}>들어가기</button>
        <button className="secondary" onClick={onExit}>뒤로</button>
        {err && <p className="err" role="alert" data-testid="pin-error">{err}</p>}
      </section>
    );
  return (
    <ScreenGuard child={child}>
      <nav>
        <button className="secondary" data-testid="tab-today" onClick={() => setTab("today")}>오늘의 한자</button>
        <button className="secondary" data-testid="tab-exam" onClick={() => setTab("exam")}>모의고사</button>
        <button className="secondary" data-testid="tab-chat" onClick={() => setTab("chat")}>한자 친구</button>
        <button className="secondary" onClick={onExit}>나가기</button>
      </nav>
      {tab === "today" && <Today child={child} />}
      {tab === "exam" && <Exam child={child} />}
      {tab === "chat" && <Chat child={child} />}
    </ScreenGuard>
  );
}

function Today({ child }: { child: Child }) {
  const [data, setData] = useState<{ new: Card[]; reviews: Card[] } | null>(null);
  const [tracing, setTracing] = useState<Card | null>(null);
  const [done, setDone] = useState<string[]>([]);
  const [msg, setMsg] = useState("");
  const load = () => api(`/children/${child.id}/today`).then(setData)
    .catch((e) => setMsg(e instanceof ApiError && e.status === 423 ? "지금은 쉬는 시간이에요." : "불러오지 못했어요."));
  useEffect(() => { load(); }, []);
  if (tracing)
    return <Tracing child={child} card={tracing} onDone={async (ok) => {
      if (ok) { await api(`/children/${child.id}/today/${tracing.char}/complete`, { body: {} }).catch(() => undefined); setDone((d) => [...d, tracing.char]); }
      setTracing(null);
    }} />;
  if (!data) return <p>{msg || "불러오는 중…"}</p>;
  return (
    <section className="card" aria-label="오늘의 한자">
      <h2>오늘의 한자</h2>
      {data.new.map((c) => (
        <div key={c.char} data-testid="today-card">
          <span style={{ fontSize: 40 }}>{c.char}</span> <span data-testid="reading">{reading(c)}</span>
          {done.includes(c.char) ? <span data-testid={`done-${c.char}`}> ✅</span>
            : c.tracing === "unavailable" ? <small> (획순 데이터가 없어 따라쓰기는 쉬어요)</small>
            : <button data-testid={`trace-${c.char}`} onClick={() => setTracing(c)}>따라 쓰기</button>}
        </div>
      ))}
      <h2>복습</h2>
      {data.reviews.length === 0 && <p>오늘 복습할 한자가 없어요.</p>}
      {data.reviews.map((c) => (
        <div key={c.char} data-testid="review-card">{c.char} {reading(c)}
          <button onClick={async () => { await api(`/children/${child.id}/reviews/${c.char}`, { body: { correct: true } }); load(); }}>알아요</button>
          <button className="secondary" onClick={async () => { await api(`/children/${child.id}/reviews/${c.char}`, { body: { correct: false } }); load(); }}>몰라요</button>
        </div>
      ))}
    </section>
  );
}
