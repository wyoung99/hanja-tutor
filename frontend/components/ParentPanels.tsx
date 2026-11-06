"use client";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError, passwordError, pct } from "../lib/api";
import { disablePush, enablePush, pushState, PUSH_STATE_TEXT, type PushState } from "../lib/push";
import { gradeLabel } from "../lib/strokes";
import type { Child } from "./ChildPanels";
import { Alert, Badge, Bubble, Button, Card, CardTitle, Check, cx, Field, inputClass, Muted, ProgressBar } from "./ui";

const err = (e: unknown) => (e instanceof ApiError ? (e.detail?.message ?? e.detail?.code ?? "error") : "error");

// ---------------------------------------------------------------- child card (AC2, AC3, AC4, AC11)
export function ChildCard({ k, bodyName, reload }: { k: Child & { login_id?: string; exam_date?: string | null }; bodyName: string; reload: () => void }) {
  const [open, setOpen] = useState<"" | "plan" | "time" | "password">("");
  const [pw, setPw] = useState("");
  const [msg, setMsg] = useState("");
  async function toggleConsent() {
    await api(`/children/${k.id}/ai-consent`, { method: "PUT", body: { ai_consent: !k.ai_consent } });
    reload();
  }
  async function resetPassword() {
    const local = passwordError(pw, "child");
    if (local) return setMsg(local);
    try {
      await api(`/children/${k.id}/reset-password`, { body: { password: pw } });
      setPw(""); setMsg("비밀번호를 바꿨어요. 잠금이 풀렸어요. 아이 기기에서 새 비밀번호로 다시 로그인해요."); setOpen("");
      reload();
    } catch (e) { setMsg(err(e)); }
  }
  async function remove() {
    if (!confirm(`${k.nickname} 계정과 학습 기록·대화를 영구 삭제할까요?`)) return;
    await api(`/children/${k.id}`, { method: "DELETE" });
    reload();
  }
  const toggle = (which: "plan" | "time" | "password") => ({
    "aria-expanded": open === which,
    onClick: () => setOpen(open === which ? "" : which),
    className: cx(open === which && "border-brand-500 bg-brand-50 dark:border-brand-500 dark:bg-brand-900/50"),
  });
  return (
    <Card data-testid="child-card" className="space-y-4">
      <div className="flex items-start gap-3">
        <span aria-hidden="true" className="inline-flex size-12 shrink-0 items-center justify-center rounded-2xl bg-warning-100 text-xl font-bold text-warning-900 dark:bg-warning-900/50 dark:text-warning-100">
          {k.nickname.slice(0, 1)}
        </span>
        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <strong className="text-lg">{k.nickname}</strong>
            <small className="text-sm text-muted" data-testid={`login-id-${k.nickname}`}>(아이디 {k.login_id})</small>
          </p>
          <p className="mt-1 flex flex-wrap items-center gap-1.5">
            <Badge tone="brand">{bodyName} {k.target[1] ? gradeLabel(k.target[1]) : ""}</Badge>
            {k.locked && <Badge tone="danger" data-testid={`locked-${k.nickname}`}> 🔒 비밀번호 5회 오류로 잠김</Badge>}
          </p>
        </div>
      </div>
      <div className="grid grid-cols-1 gap-2 min-[420px]:grid-cols-3">
        <Button variant="secondary" size="sm" data-testid={`plan-${k.nickname}`} {...toggle("plan")}>학습 계획</Button>
        <Button variant="secondary" size="sm" data-testid={`time-${k.nickname}`} {...toggle("time")}>사용 시간</Button>
        <Button variant="secondary" size="sm" data-testid={`password-${k.nickname}`} {...toggle("password")}>비밀번호 재설정</Button>
      </div>
      {open === "password" && (
        <div className={subPanel}>
          <input className={inputClass} data-testid={`newpassword-${k.nickname}`} type="password" autoComplete="new-password" placeholder="새 비밀번호 (4자 이상)" value={pw} onChange={(e) => setPw(e.target.value)} />
          <Button data-testid={`resetpassword-${k.nickname}`} onClick={resetPassword}>비밀번호 바꾸고 잠금 풀기</Button>
        </div>
      )}
      {open === "plan" && <PlanEditor childId={k.id} />}
      {open === "time" && <ScreenTimeEditor childId={k.id} />}
      {msg && <Alert tone="info" data-testid={`child-msg-${k.nickname}`}>{msg}</Alert>}
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line pt-3">
        <Check data-testid={`consent-${k.nickname}`} checked={k.ai_consent} onChange={toggleConsent} className="text-sm">AI 처리 동의 (끄면 챗봇만 꺼져요)</Check>
        <Button variant="danger" size="sm" data-testid={`delete-${k.nickname}`} onClick={remove}>자녀 계정 삭제</Button>
      </div>
    </Card>
  );
}

const subPanel = "space-y-3 rounded-2xl border border-line bg-soft/60 p-4";

function PlanEditor({ childId }: { childId: string }) {
  const [plan, setPlan] = useState<any>(null);
  const [date, setDate] = useState("");
  const [count, setCount] = useState("");
  const [msg, setMsg] = useState("");
  useEffect(() => {
    api(`/children/${childId}/plan`).then((p) => { setPlan(p); setDate(p.exam_date ?? ""); setCount(p.daily_count?.toString() ?? ""); }).catch((e) => setMsg(err(e)));
  }, [childId]);
  async function save() {
    setMsg("");
    try {
      setPlan(await api(`/children/${childId}/plan`, { method: "PUT", body: { exam_date: date || null, daily_count: count ? Number(count) : null } }));
      setMsg("저장했어요.");
    } catch (e) { setMsg(err(e)); }
  }
  return (
    <div className={subPanel} data-testid="plan-editor">
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="시험일" hint="(비우면 90일 계획)"><input className={inputClass} type="date" data-testid="plan-date" value={date} onChange={(e) => setDate(e.target.value)} /></Field>
        <Field label="하루 새 한자 수" hint="(3~15, 비우면 자동 균등 분배)"><input className={inputClass} type="number" min={3} max={15} data-testid="plan-count" value={count} onChange={(e) => setCount(e.target.value)} /></Field>
      </div>
      <Button data-testid="plan-save" onClick={save}>계획 저장</Button>
      {plan && <Alert tone={plan.feasible ? "brand" : "warning"} data-testid="plan-summary">오늘 새 한자 {plan.today_quota}자 · 남은 한자 {plan.remaining}자 · 남은 날 {plan.days_left}일{plan.feasible ? "" : " (하루 15자를 넘어 시험일까지 다 배우기 어려워요)"}</Alert>}
      {msg && <p className="text-sm font-semibold text-success-700 dark:text-success-300" data-testid="plan-msg">{msg}</p>}
    </div>
  );
}

function ScreenTimeEditor({ childId }: { childId: string }) {
  const [daily, setDaily] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [msg, setMsg] = useState("");
  useEffect(() => {
    api(`/children/${childId}/screentime`).then((s) => {
      setDaily(s.daily_minutes?.toString() ?? "");
      if (s.windows[0]) { setFrom(s.windows[0][0]); setTo(s.windows[0][1]); }
    });
  }, [childId]);
  async function save() {
    setMsg("");
    try {
      await api(`/children/${childId}/screentime`, { method: "PUT", body: { daily_minutes: daily ? Number(daily) : null, windows: from && to ? [[from, to]] : [] } });
      setMsg("저장했어요.");
    } catch (e) { setMsg(err(e)); }
  }
  return (
    <div className={subPanel} data-testid="screentime-editor">
      <Field label="하루 총 사용 시간(분, 비우면 제한 없음)"><input className={inputClass} type="number" min={1} data-testid="st-daily" value={daily} onChange={(e) => setDaily(e.target.value)} /></Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="사용 가능 시간대 시작"><input className={inputClass} type="time" data-testid="st-from" value={from} onChange={(e) => setFrom(e.target.value)} /></Field>
        <Field label="사용 가능 시간대 끝"><input className={inputClass} type="time" data-testid="st-to" value={to} onChange={(e) => setTo(e.target.value)} /></Field>
      </div>
      <Button data-testid="st-save" onClick={save}>사용 시간 저장</Button>
      {msg && <p className="text-sm font-semibold text-success-700 dark:text-success-300" data-testid="st-msg">{msg}</p>}
    </div>
  );
}

// ---------------------------------------------------------------- reports + conversations + safety (AC8, AC12)
function Stat({ label, value, bar }: { label: string; value: string; bar?: number | null }) {
  return (
    <li className="rounded-2xl border border-line bg-soft/60 p-4">
      <span className="block text-sm font-semibold text-muted">{label}: </span>
      <span className="mt-1 block text-2xl font-extrabold tracking-tight">{value}</span>
      {bar !== undefined && <ProgressBar className="mt-3" value={bar} label={label} />}
    </li>
  );
}

export function Reports({ kids }: { kids: Child[] }) {
  const [sel, setSel] = useState(kids[0]?.id ?? "");
  const k = kids.find((x) => x.id === sel) ?? kids[0];
  const [basic, setBasic] = useState<any>(null);
  const [detail, setDetail] = useState<any>(null);
  const [msg, setMsg] = useState("");
  useEffect(() => {
    setDetail(null); setMsg("");
    if (k) api(`/children/${k.id}/report/basic`).then(setBasic).catch(() => undefined);
  }, [k?.id]);
  if (!k) return <Alert tone="info" className="mx-auto max-w-xl text-base">자녀를 먼저 만들어 주세요.</Alert>;
  async function loadDetail() {
    setMsg("");
    try { setDetail(await api(`/children/${k.id}/report/detailed`)); }
    catch (e) { setMsg(e instanceof ApiError && e.status === 402 ? "상세 리포트는 구독 전용이에요." : "오류가 발생했어요."); }
  }
  return (
    <div className="space-y-6">
      {kids.length > 1 && (
        <Field label="자녀 선택" className="max-w-xs">
          <select className={inputClass} data-testid="report-child" value={k.id} onChange={(e) => setSel(e.target.value)}>
            {kids.map((x) => <option key={x.id} value={x.id}>{x.nickname}</option>)}
          </select>
        </Field>
      )}
      <Card aria-label="리포트">
        <CardTitle aside={<Button variant="secondary" size="sm" data-testid="detail-report" onClick={loadDetail}>상세 리포트 보기</Button>}>
          {k.nickname} 기본 리포트 (무료)
        </CardTitle>
        {basic && (
          <ul data-testid="basic-report" className="grid gap-3 sm:grid-cols-3">
            <Stat label="학습 시간" value={`${basic.study_minutes_total}분`} />
            <Stat label="오늘의 한자 완료율" value={pct(basic.todays_hanja.completion_rate)} bar={basic.todays_hanja.completion_rate} />
            <Stat label="복습 정답률" value={pct(basic.review.accuracy)} bar={basic.review.accuracy} />
          </ul>
        )}
        {msg && <Alert tone="warning" role="alert" className="mt-4" data-testid="detail-msg">{msg}</Alert>}
        {detail && (
          <dl data-testid="detail-body" className="mt-4 grid gap-x-6 gap-y-3 rounded-2xl border border-line p-4 text-[15px] sm:grid-cols-[auto_1fr]">
            <dt className="font-semibold text-muted">취약 한자:</dt><dd className="font-hanja text-lg">{detail.weak_hanja.slice(0, 10).map((w: any) => w.char).join(" ") || "-"}</dd>
            <dt className="font-semibold text-muted">모의고사 점수 추이:</dt><dd>{detail.mock_exam_trend.map((t: any) => t.score).join(" → ") || "-"}</dd>
            <dt className="font-semibold text-muted">합격 가능성:</dt><dd>{detail.pass_likelihood ? (detail.pass_likelihood.likely_pass ? "높음" : "노력 필요") + ` (최근 평균 ${detail.pass_likelihood.average_score.toFixed(1)}점 / 합격선 ${detail.pass_likelihood.pass_score}점)` : "-"}</dd>
            <dt className="font-semibold text-muted">쓰기 완료 한자:</dt><dd>{detail.writing_completed_count}자</dd>
          </dl>
        )}
      </Card>
      <div className="grid items-start gap-6 lg:grid-cols-2">
        <Conversations child={k} />
        <SafetyEvents child={k} />
      </div>
    </div>
  );
}

function Conversations({ child }: { child: Child }) {
  const [threads, setThreads] = useState<any[]>([]);
  const [open, setOpen] = useState<{ id: string; messages: any[] } | null>(null);
  const load = useCallback(() => api(`/children/${child.id}/conversations`).then(setThreads).catch(() => undefined), [child.id]);
  useEffect(() => { load(); setOpen(null); }, [load]);
  async function view(id: string) {
    const r = await api(`/children/${child.id}/conversations/${id}`);
    setOpen({ id, messages: r.messages });
  }
  async function del(id: string) {
    await api(`/children/${child.id}/conversations/${id}`, { method: "DELETE" });
    if (open?.id === id) setOpen(null);
    load();
  }
  return (
    <Card aria-label="대화 열람" data-testid="parent-conversations">
      <CardTitle aside={<Badge tone="brand">{threads.length}</Badge>}>대화 열람 (무료)</CardTitle>
      {threads.length === 0 && <Muted>저장된 대화가 없어요.</Muted>}
      <ul className="divide-y divide-line">
        {threads.map((t) => (
          <li key={t.thread_id} className="flex items-center gap-2 py-1.5">
            <button type="button" data-testid="pc-thread" aria-current={open?.id === t.thread_id ? "true" : undefined} onClick={() => view(t.thread_id)}
              className={cx("min-h-11 flex-1 truncate rounded-lg px-2 text-left text-[15px] font-medium hover:bg-soft", open?.id === t.thread_id && "bg-brand-50 text-brand-800 dark:bg-brand-900/50 dark:text-brand-200")}>
              {t.title || "(제목 없음)"} · <span className="text-sm text-muted">{t.last_message_at.slice(0, 16).replace("T", " ")}</span>
            </button>
            <Button variant="danger" size="sm" data-testid="pc-delete" onClick={() => del(t.thread_id)}>삭제</Button>
          </li>
        ))}
      </ul>
      {open && (
        <div data-testid="pc-messages" className="mt-4 flex max-h-96 flex-col gap-3 overflow-y-auto rounded-2xl border border-line bg-bg p-3">
          {open.messages.map((m, i) => <Bubble key={i} role={m.role === "user" ? "user" : "bot"}>{m.content}</Bubble>)}
        </div>
      )}
    </Card>
  );
}

const CATEGORY: Record<string, string> = { pii: "개인정보", profanity: "욕설", violence: "폭력·위험", sexual: "성적 내용", self_harm: "자해", drugs: "약물", harmful: "유해" };

function SafetyEvents({ child }: { child: Child }) {
  const [events, setEvents] = useState<any[]>([]);
  useEffect(() => { api(`/children/${child.id}/safety-events`).then(setEvents).catch(() => undefined); }, [child.id]);
  return (
    <Card aria-label="안전 알림" data-testid="safety-events">
      <CardTitle aside={<Badge tone={events.length ? "danger" : "success"}>{events.length}건</Badge>}>차단된 입력 기록 (원문은 저장하지 않아요)</CardTitle>
      {events.length === 0 && <Muted>차단된 입력이 없어요.</Muted>}
      <ul className="space-y-2">{events.map((e) => (
        <li key={e.id} data-testid="safety-event" className="rounded-xl border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-900 dark:border-danger-900 dark:bg-danger-950/50 dark:text-danger-100">
          {e.at.slice(0, 16).replace("T", " ")} · {CATEGORY[e.category] ?? e.category} · 같은 대화 {e.thread_count}회째{e.thread_count >= 3 ? " (오늘 챗봇 잠김)" : ""}
        </li>
      ))}</ul>
    </Card>
  );
}

// ---------------------------------------------------------------- alerts: push inbox, extension requests (AC11, AC12)
const KIND: Record<string, string> = {
  safety_event: "유해·개인정보 입력이 차단됐어요", extension_request: "사용 시간 연장 요청", weekly_summary: "주간 학습 요약",
  usage_limit_reached: "무료 한도 도달", dormancy_notice: "휴면 전환 예정 안내", payment_failed: "결제 실패",
  subscription_ended: "구독 종료", refund_decided: "환불 결과", push_test: "알림 테스트",
};

// ---------------------------------------------------------------- Web Push settings (AC8, AC11, AC12, AC13)
export function PushSettings() {
  const [state, setState] = useState<PushState | "loading">("loading");
  const [msg, setMsg] = useState("");
  useEffect(() => { pushState().then(setState).catch(() => setState("error")); }, []);
  async function on() {
    setMsg("");
    try { setState(await enablePush()); } catch (e) { setState("error"); setMsg(e instanceof Error ? e.message : "error"); }
  }
  async function off() {
    setMsg("");
    try { setState(await disablePush()); } catch { setState("error"); }
  }
  async function test() {
    const r = await api("/parents/me/push-test", { body: {} }).catch(() => null);
    setMsg(r ? (r.push === "sent" ? "테스트 알림을 보냈어요." : r.email ? "푸시를 보내지 못해 이메일로 보냈어요." : "알림함에 넣었어요.") : "보내지 못했어요.");
  }
  return (
    <Card aria-label="푸시 알림" data-testid="push-settings">
      <CardTitle>기기 푸시 알림</CardTitle>
      <Muted className="-mt-2 mb-4">안전 알림·연장 요청·주간 요약(구독)·무료 한도 안내를 이 기기로 받아요. 아이 대화 원문은 알림에 담기지 않아요.</Muted>
      <p className="mb-4" data-testid="push-state" data-state={state}>
        <Badge tone={state === "on" ? "success" : state === "error" ? "danger" : "neutral"} className="px-3 py-1 text-sm">{state === "loading" ? "확인 중…" : PUSH_STATE_TEXT[state]}</Badge>
      </p>
      <div className="flex flex-wrap gap-2">
        {state !== "on" && state !== "unsupported" && state !== "not_configured" && (
          <Button data-testid="push-on" onClick={on}>푸시 알림 켜기</Button>)}
        {state === "on" && <Button variant="secondary" data-testid="push-off" onClick={off}>푸시 알림 끄기</Button>}
        <Button variant="secondary" data-testid="push-test" onClick={test}>테스트 알림 보내기</Button>
      </div>
      {msg && <Alert tone="info" className="mt-4" data-testid="push-msg">{msg}</Alert>}
    </Card>
  );
}

export function Alerts() {
  const [reqs, setReqs] = useState<any[]>([]);
  const [notes, setNotes] = useState<any[]>([]);
  const load = useCallback(async () => {
    setReqs(await api("/parents/me/extension-requests").catch(() => []));
    const n = await api("/parents/me/notifications").catch(() => []);
    setNotes((prev) => {
      if (n.length > prev.length) showSystemNotifications(n.slice(prev.length));
      return n;
    });
  }, []);
  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [load]);
  async function resolve(id: string, approve: boolean) {
    await api(`/parents/me/extension-requests/${id}`, { body: { approve } });
    load();
  }
  return (
    <div className="grid items-start gap-6 lg:grid-cols-2">
      <div className="space-y-6">
        <Card aria-label="연장 요청">
          <CardTitle aside={<Badge tone={reqs.length ? "warning" : "neutral"}>{reqs.length}</Badge>}>사용 시간 연장 요청</CardTitle>
          {reqs.length === 0 && <Muted>대기 중인 요청이 없어요.</Muted>}
          <ul className="space-y-2">
            {reqs.map((r) => (
              <li key={r.id} data-testid="ext-request" className="flex flex-wrap items-center gap-3 rounded-2xl border border-warning-200 bg-warning-50 p-3 dark:border-warning-900 dark:bg-warning-950/40">
                <span className="flex-1 font-semibold">{r.nickname}: {r.minutes}분 더 쓰고 싶어요</span>
                <span className="flex gap-2">
                  <Button size="sm" variant="success" data-testid="ext-approve" onClick={() => resolve(r.id, true)}>허락</Button>
                  <Button size="sm" variant="secondary" data-testid="ext-reject" onClick={() => resolve(r.id, false)}>거절</Button>
                </span>
              </li>
            ))}
          </ul>
        </Card>
        <Alert tone="brand">아이 기기의 &lsquo;쉬는 시간&rsquo; 화면에서 부모님 계정 비밀번호를 입력하면 바로 시간을 더 줄 수 있어요.</Alert>
        <PushSettings />
      </div>
      <Card aria-label="알림">
        <CardTitle aside={<Badge tone="brand">{notes.length}</Badge>}>알림</CardTitle>
        {notes.length === 0 && <Muted>아직 받은 알림이 없어요.</Muted>}
        <ul className="divide-y divide-line">{[...notes].reverse().map((n) => (
          <li key={n.seq} data-testid={`note-${n.kind}`} className="flex gap-3 py-3 text-[15px]">
            <span aria-hidden="true" className={cx("mt-1.5 size-2.5 shrink-0 rounded-full",
              n.kind === "safety_event" || n.kind === "payment_failed" ? "bg-danger-500" : n.kind === "extension_request" || n.kind === "usage_limit_reached" ? "bg-warning-500" : "bg-brand-500")} />
            <span>{KIND[n.kind] ?? n.kind}{n.payload?.message ? ` — ${n.payload.message}` : ""}{n.payload?.nickname ? ` (${n.payload.nickname})` : ""}</span>
          </li>
        ))}</ul>
      </Card>
    </div>
  );
}

async function showSystemNotifications(items: any[]) {
  try {
    if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
    const reg = await navigator.serviceWorker?.ready;
    if (await reg?.pushManager?.getSubscription()) return; // real Web Push already shows these
    for (const n of items) await reg?.showNotification("한자 튜터", { body: KIND[n.kind] ?? n.kind, icon: "/icon-192.png", tag: `${n.kind}-${n.seq}` });
  } catch { /* notifications are best-effort */ }
}

// ---------------------------------------------------------------- account deletion (AC3)
export function Account({ onDeleted }: { onDeleted: () => void }) {
  const [msg, setMsg] = useState("");
  async function del() {
    if (!confirm("계정을 삭제하면 모든 자녀의 학습 기록과 대화가 즉시 삭제돼요. 결제 기록은 법에 따라 5년간 따로 보관돼요. 삭제할까요?")) return;
    try { await api("/parents/me", { method: "DELETE" }); onDeleted(); }
    catch (e) { setMsg(err(e)); }
  }
  return (
    <Card aria-label="계정" className="mx-auto max-w-2xl">
      <CardTitle>계정</CardTitle>
      <Muted>1년 동안 접속하지 않으면 30일 전에 안내한 뒤 휴면 계정으로 바뀌어요. 아이디와 비밀번호로 다시 로그인하면 복구돼요.</Muted>
      <div className="mt-6 rounded-2xl border border-danger-200 bg-danger-50/60 p-4 dark:border-danger-900 dark:bg-danger-950/30">
        <h3 className="font-bold text-danger-800 dark:text-danger-200">위험 구역</h3>
        <p className="mt-1 mb-3 text-sm text-danger-800 dark:text-danger-200">삭제하면 모든 자녀의 학습 기록과 대화가 즉시 사라져요.</p>
        <Button variant="danger" data-testid="delete-account" onClick={del}>계정 삭제</Button>
      </div>
      {msg && <Alert tone="danger" className="mt-4">{msg}</Alert>}
    </Card>
  );
}
