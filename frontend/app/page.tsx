"use client";
import { useEffect, useState, type ReactNode } from "react";
import {
  api, ApiError, CHILD_MENUS, loadAuth, loginIdError, LOGOUT_EVENT, PARENT_MENUS, passLine, passwordError, session,
  setAuth, won, type Auth,
} from "../lib/api";
import { gradeLabel } from "../lib/strokes";
import { Chat, Exam, reading, ScreenGuard, Tracing, type Card as HanjaCard, type Child } from "../components/ChildPanels";
import { Account, Alerts, ChildCard, Reports } from "../components/ParentPanels";
import { LoginForm } from "../components/Auth";
import {
  Alert, AuthShell, Badge, Button, Card, CardTitle, Check, cx, Field, inputClass, Logo, Muted, ProgressBar,
} from "../components/ui";

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
  if (auth?.role === "parent") return <Parent onLogout={logout} onDeleted={() => { setAuth(null); setAuthState(null); }} />;
  if (auth?.role === "child") return <ChildApp childId={auth.accountId} onLogout={logout} />;
  return <AuthShell>{ready && <LoginForm onLoggedIn={setAuthState} />}</AuthShell>;
}

// ---------------------------------------------------------------- shared app header
function AppHeader({ who, onLogout, children, wide }: { who: ReactNode; onLogout: () => void; children?: ReactNode; wide?: boolean }) {
  return (
    <header className="sticky top-0 z-20 border-b border-line bg-card/90 backdrop-blur supports-[backdrop-filter]:bg-card/80">
      <div className={cx("mx-auto flex h-16 items-center gap-3 px-4", wide ? "max-w-6xl" : "max-w-4xl")}>
        <Logo size="sm" />
        <h1 className="text-lg font-extrabold tracking-tight">한자 튜터</h1>
        {who}
        <div className="flex-1" />
        <Button variant="ghost" size="sm" data-testid="logout" onClick={onLogout}>로그아웃</Button>
      </div>
      {children}
    </header>
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
    <div className="min-h-dvh">
      <AppHeader wide who={<Badge tone="brand" className="hidden sm:inline-flex">학부모</Badge>} onLogout={onLogout}>
        <nav data-testid="parent-menu" aria-label="관리 메뉴" className="no-scrollbar mx-auto flex max-w-6xl gap-1 overflow-x-auto px-2 sm:px-4">
          {PARENT_MENUS.map((m) => (
            <button key={m.key} type="button" data-testid={`tab-${m.key}`} aria-current={tab === m.key ? "page" : undefined}
              onClick={() => { setTab(m.key); if (m.key === "kids") reload(); }}
              className={cx("relative min-h-11 shrink-0 whitespace-nowrap rounded-t-lg px-3 text-[15px] font-semibold transition-colors",
                "after:absolute after:inset-x-2 after:bottom-0 after:h-0.5 after:rounded-full",
                tab === m.key ? "text-brand-700 after:bg-brand-600 dark:text-brand-300 dark:after:bg-brand-400" : "text-muted hover:text-ink")}>
              {m.label}
            </button>
          ))}
        </nav>
      </AppHeader>
      <main className="mx-auto max-w-6xl px-4 py-6 sm:py-8">
        {tab === "kids" && (
          <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
            <Card aria-label="자녀 계정 만들기" className="lg:sticky lg:top-32">
              <CardTitle>자녀 계정 만들기</CardTitle>
              <Muted className="-mt-2 mb-5">아이는 스스로 가입할 수 없어요. 여기서 정한 아이디·비밀번호로 아이 기기에서 로그인해요.</Muted>
              <div className="space-y-4">
                <Field label="닉네임" hint="(실명 대신)"><input className={inputClass} data-testid="nick" value={nick} onChange={(e) => setNick(e.target.value)} /></Field>
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field label="자녀 아이디" hint="(영문·숫자 4~20자)"><input className={inputClass} data-testid="child-login-id" autoComplete="off" value={kidId} onChange={(e) => setKidId(e.target.value)} /></Field>
                  <Field label="자녀 비밀번호" hint="(4자 이상)"><input className={inputClass} data-testid="child-password" type="password" autoComplete="new-password" value={kidPw} onChange={(e) => setKidPw(e.target.value)} /></Field>
                </div>
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field label="시험 기관">
                    <select className={inputClass} data-testid="body" value={body} onChange={(e) => { setBody(e.target.value); setGrade(catalog.find((c) => c.body === e.target.value)?.grades[0] ?? ""); }}>
                      {catalog.map((c) => <option key={c.body} value={c.body}>{c.name}</option>)}
                    </select>
                  </Field>
                  <Field label="급수">
                    <select className={inputClass} data-testid="grade" value={grade} onChange={(e) => setGrade(e.target.value)}>
                      {grades.map((g) => (
                        <option key={g} value={g}>{gradeLabel(g)}{entry?.exam[g]?.status === "format_pending" ? " (모의고사 형식 준비 중)" : ""}</option>
                      ))}
                    </select>
                  </Field>
                </div>
                {info?.status === "available" && (
                  <Alert tone="info" data-testid="grade-format">
                    모의고사: {info.questions}문항 · {info.minutes}분 · 합격 {passLine({ ...info, pass_score: info.pass_score ?? 0 })}
                    {info.all_multiple_choice ? " · 모두 객관식" : ""}
                    {info.notice && <><br /><small data-testid="grade-notice" className="opacity-90">{info.notice}</small></>}
                  </Alert>
                )}
                {pendingNote && <Alert tone="warning" data-testid="grade-note">이 급수는 공식 모의고사 형식을 확인하는 중이라 모의고사는 아직 볼 수 없어요. 오늘의 한자·복습·따라쓰기는 모두 할 수 있어요.</Alert>}
                <Check data-testid="ai-consent" checked={ai} onChange={(e) => setAi(e.target.checked)}>대화·학습 기록의 AI 처리에 동의합니다 (미동의 시 챗봇만 꺼져요)</Check>
                <Button className="w-full" size="lg" data-testid="add-child" onClick={addChild}>자녀 계정 만들기</Button>
                {err && <Alert tone="danger" role="alert" data-testid="add-child-error">{err}</Alert>}
                {created && <Alert tone="success" data-testid="child-created">{created}</Alert>}
              </div>
            </Card>
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <h2 className="text-lg font-bold">우리 아이들</h2>
                <Badge tone="brand">{kids.length}명</Badge>
              </div>
              {kids.length === 0 && (
                <div className="rounded-(--radius-card) border-2 border-dashed border-line p-8 text-center text-sm text-muted">
                  아직 만든 자녀 계정이 없어요. 왼쪽(모바일은 위쪽)에서 만들어 주세요.
                </div>
              )}
              {kids.map((k) => (
                <ChildCard key={k.id} k={k} bodyName={catalog.find((c) => c.body === k.target[0])?.name ?? k.target[0]}
                  reload={reload} />
              ))}
            </div>
          </div>
        )}
        {tab === "report" && <Reports kids={kids} />}
        {tab === "alerts" && <Alerts />}
        {tab === "billing" && <Billing />}
        {tab === "account" && <Account onDeleted={onDeleted} />}
      </main>
    </div>
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
    <Card aria-label="구독" className="mx-auto max-w-3xl">
      <CardTitle aside={sub && <Badge tone={subscribed ? "success" : "neutral"}>{subscribed ? "구독 중" : "무료 플랜"}</Badge>}>구독</CardTitle>
      <Alert tone="brand" className="mb-5">무료 플랜에서도 오늘의 한자·복습·따라쓰기·기본 리포트·안전 기능은 항상 무제한이에요.</Alert>
      {!subscribed && plans.length > 0 && (
        <div className="grid gap-4 sm:grid-cols-2">
          {plans.map((p) => (
            <div key={p.id} className="flex flex-col justify-between gap-4 rounded-2xl border border-line bg-soft/60 p-5">
              <span data-testid={`price-${p.id}`} className="flex flex-col">
                <span className="text-sm font-semibold text-muted">{p.name}</span>{" "}
                <span className="text-2xl font-extrabold tracking-tight">{won(p.price_krw)}</span>
              </span>
              <Button data-testid={`pay-${p.id}`} onClick={() => pay(p.id)}>결제하기</Button>
            </div>
          ))}
        </div>
      )}
      {subscribed && (
        <div className="space-y-4">
          <Alert tone="success" data-testid="sub-status">구독 상태: {sub.status} ({sub.plan_id}) · {sub.period_end?.slice(0, 10)}까지</Alert>
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={cancel}>해지</Button>
            <Button data-testid="refund" onClick={requestRefund}>7일 환불 요청</Button>
          </div>
        </div>
      )}
      {refund && (
        <div data-testid="refund-result" className="mt-5 space-y-3 rounded-2xl border border-line p-4">
          <p className="font-semibold">상태: <span data-testid="refund-status"><Badge tone={refund.status === "auto_approved" ? "success" : refund.status === "auto_rejected" ? "danger" : "info"}>{refund.status}</Badge></span></p>
          {refund.status === "auto_rejected" && (
            <>
              <p data-testid="refund-reason" className="text-sm">거절 사유: {refund.reason}</p>
              <ul className="list-disc space-y-1 pl-5 text-sm text-muted">{refund.usage_records.map((u: any, i: number) => <li key={i}>{u.label}</li>)}</ul>
              {refund.can_request_review && <Button variant="secondary" data-testid="refund-review" onClick={requestReview}>운영자 검토 요청</Button>}
            </>
          )}
        </div>
      )}
      {sub && sub.status === "free" && !subscribed && refund?.status === "auto_approved" && <Alert tone="success" className="mt-4">환불이 완료되어 구독이 종료됐어요.</Alert>}
      {err && <Alert tone="danger" role="alert" className="mt-4">{err}</Alert>}
    </Card>
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
const TAB_ICON: Record<(typeof CHILD_MENUS)[number]["key"], string> = {
  today: "M12 3v2m0 14v2M5.6 5.6 7 7m10 10 1.4 1.4M3 12h2m14 0h2M5.6 18.4 7 17m10-10 1.4-1.4M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Z",
  review: "M4 12a8 8 0 0 1 14-5.3L20 9M20 4v5h-5M20 12a8 8 0 0 1-14 5.3L4 15m0 5v-5h5",
  tracing: "m15 5 4 4M4 20l1-5L16 4a2.1 2.1 0 0 1 3 3L8 18l-4 2Z",
  exam: "M9 4h6m-6 0a1 1 0 0 0-1 1v1h8V5a1 1 0 0 0-1-1M9 4H7a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2h-2M9 13l2 2 4-4",
  chat: "M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12Z",
};

function ChildApp({ childId, onLogout }: { childId: string; onLogout: () => void }) {
  const [child, setChild] = useState<Child | null>(null);
  const [tab, setTab] = useState<(typeof CHILD_MENUS)[number]["key"]>("today");
  useEffect(() => {
    api("/auth/me").then((m) => setChild({ id: m.account_id, nickname: m.nickname, ai_consent: m.ai_consent, target: m.target }))
      .catch(() => undefined);
  }, [childId]);
  return (
    <div className="min-h-dvh bg-linear-to-b from-brand-50 to-bg pb-28 sm:pb-10 dark:from-brand-950/40">
      <AppHeader who={<Badge tone="warning" className="hidden sm:inline-flex">학습</Badge>} onLogout={onLogout} />
      <main className="mx-auto max-w-4xl px-4 py-5 sm:py-8">
        {!child ? <Loading /> : (
          <ScreenGuard child={child}>
            <p data-testid="child-hello" className="mb-4 text-xl font-bold tracking-tight sm:text-2xl">
              {child.nickname}, 안녕! 오늘도 한자 공부해요. <span aria-hidden="true">👋</span>
            </p>
            <nav data-testid="child-menu" aria-label="학습 메뉴"
              className={cx("fixed inset-x-0 bottom-0 z-30 grid grid-cols-5 border-t border-line bg-card/95 pb-[env(safe-area-inset-bottom)] backdrop-blur",
                "sm:static sm:mb-6 sm:flex sm:gap-1 sm:rounded-2xl sm:border sm:p-1.5 sm:shadow-(--shadow-card)")}>
              {CHILD_MENUS.map((m) => {
                const on = tab === m.key;
                return (
                  <button key={m.key} type="button" data-testid={`tab-${m.key}`} aria-current={on ? "page" : undefined} onClick={() => setTab(m.key)}
                    className={cx("flex min-h-16 flex-col items-center justify-center gap-1 px-1 text-xs font-semibold transition-colors",
                      "sm:min-h-12 sm:flex-1 sm:flex-row sm:gap-2 sm:rounded-xl sm:text-[15px]",
                      on ? "text-brand-700 dark:text-brand-300 sm:bg-brand-700 sm:text-white sm:shadow-sm dark:sm:bg-brand-400 dark:sm:text-brand-950"
                        : "text-muted hover:text-ink sm:hover:bg-soft")}>
                    <svg aria-hidden="true" viewBox="0 0 24 24" className={cx("size-6 sm:size-5", on && "max-sm:scale-110")} fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
                      <path d={TAB_ICON[m.key]} />
                    </svg>
                    {m.label}
                  </button>
                );
              })}
            </nav>
            {(tab === "today" || tab === "review" || tab === "tracing") && <Learn key={tab} child={child} mode={tab} />}
            {tab === "exam" && <Exam child={child} />}
            {tab === "chat" && <Chat child={child} />}
          </ScreenGuard>
        )}
      </main>
    </div>
  );
}

const Loading = ({ text }: { text?: string }) => (
  <p className="flex items-center gap-2 py-10 text-muted" role="status">
    <span aria-hidden="true" className="size-4 animate-spin rounded-full border-2 border-brand-300 border-t-brand-700" />
    {text || "불러오는 중…"}
  </p>
);

/** Big glyph tile used by 오늘의 한자 / 복습 / 따라쓰기. */
function Glyph({ char, size = "lg" }: { char: string; size?: "md" | "lg" }) {
  return (
    <span lang="zh-Hant" className={cx("flex shrink-0 items-center justify-center rounded-2xl border border-brand-100 bg-brand-50 font-hanja font-bold leading-none text-brand-900 dark:border-brand-900 dark:bg-brand-950/60 dark:text-brand-100",
      size === "lg" ? "size-24 text-6xl" : "size-16 text-4xl")}>
      {char}
    </span>
  );
}

/** 오늘의 한자 (new chars), 복습 (due reviews) and 따라쓰기 (every char of today's session). */
function Learn({ child, mode }: { child: Child; mode: "today" | "review" | "tracing" }) {
  const [data, setData] = useState<{ new: HanjaCard[]; reviews: HanjaCard[] } | null>(null);
  const [tracing, setTracing] = useState<HanjaCard | null>(null);
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
  if (!data) return msg ? <Alert tone="warning" className="text-base">{msg}</Alert> : <Loading />;
  const traceButton = (c: HanjaCard) => done.includes(c.char) ? <Badge tone="success" className="px-3 py-1 text-sm" data-testid={`done-${c.char}`}> ✅ 완료</Badge>
    : c.tracing === "unavailable" ? <small className="text-sm text-muted"> (획순 데이터가 없어 따라쓰기는 쉬어요)</small>
    : <Button data-testid={`trace-${c.char}`} onClick={() => setTracing(c)}>따라 쓰기</Button>;
  if (mode === "today") {
    const finished = data.new.filter((c) => done.includes(c.char)).length;
    return (
      <Card aria-label="오늘의 한자">
        <CardTitle aside={<Badge tone="brand">{data.new.length}자</Badge>}>오늘의 한자</CardTitle>
        {data.new.length > 0 && (
          <div className="mb-5">
            <div className="mb-1.5 flex justify-between text-sm text-muted"><span>오늘 따라쓰기</span><span>{finished} / {data.new.length}</span></div>
            <ProgressBar value={finished / data.new.length} tone="success" label="오늘의 한자 따라쓰기 진행" />
          </div>
        )}
        <div className="grid gap-3 sm:grid-cols-2">
          {data.new.map((c) => (
            <div key={c.char} data-testid="today-card" className="flex items-center gap-4 rounded-2xl border border-line bg-card p-3 shadow-xs">
              <Glyph char={c.char} />
              <div className="flex min-w-0 flex-1 flex-col items-start gap-2">
                <span data-testid="reading" className="text-lg font-bold">{reading(c)}</span>
                {traceButton(c)}
              </div>
            </div>
          ))}
        </div>
      </Card>
    );
  }
  if (mode === "review")
    return (
      <Card aria-label="복습">
        <CardTitle aside={<Badge tone={data.reviews.length ? "warning" : "success"}>{data.reviews.length}개 남음</Badge>}>복습</CardTitle>
        {data.reviews.length === 0 && <Alert tone="success" data-testid="no-review" className="text-base">오늘 복습할 한자가 없어요.</Alert>}
        <div className="grid gap-3 sm:grid-cols-2">
          {data.reviews.map((c) => (
            <div key={c.char} data-testid="review-card" className="flex flex-col gap-3 rounded-2xl border border-line p-3">
              <div className="flex items-center gap-4"><Glyph char={c.char} size="md" /> <span className="text-lg font-bold">{reading(c)}</span></div>
              <div className="grid grid-cols-2 gap-2">
                <Button variant="success" onClick={async () => { await api(`/children/${child.id}/reviews/${c.char}`, { body: { correct: true } }); load(); }}>알아요</Button>
                <Button variant="secondary" onClick={async () => { await api(`/children/${child.id}/reviews/${c.char}`, { body: { correct: false } }); load(); }}>몰라요</Button>
              </div>
            </div>
          ))}
        </div>
      </Card>
    );
  const all = [...data.new, ...data.reviews.filter((r) => !data.new.some((n) => n.char === r.char))];
  return (
    <Card aria-label="따라쓰기">
      <CardTitle aside={<Badge tone="brand">{all.length}자</Badge>}>따라쓰기</CardTitle>
      <div className="grid gap-3 sm:grid-cols-2">
        {all.map((c) => (
          <div key={c.char} data-testid="tracing-card" className="flex items-center gap-4 rounded-2xl border border-line p-3">
            <Glyph char={c.char} size="md" />
            <div className="flex min-w-0 flex-1 flex-col items-start gap-2"><span className="font-bold">{reading(c)}</span> {traceButton(c)}</div>
          </div>
        ))}
      </div>
    </Card>
  );
}
