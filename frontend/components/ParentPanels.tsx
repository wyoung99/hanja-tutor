"use client";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError, pct } from "../lib/api";
import { gradeLabel } from "../lib/strokes";
import type { Child } from "./ChildPanels";

const err = (e: unknown) => (e instanceof ApiError ? (e.detail?.message ?? e.detail?.code ?? "error") : "error");

// ---------------------------------------------------------------- child card (AC2, AC3, AC4, AC11)
export function ChildCard({ k, bodyName, onEnter, reload }: { k: Child & { exam_date?: string | null }; bodyName: string; onEnter: () => void; reload: () => void }) {
  const [open, setOpen] = useState<"" | "plan" | "time" | "pin">("");
  const [pin, setPin] = useState("");
  const [msg, setMsg] = useState("");
  async function toggleConsent() {
    await api(`/children/${k.id}/ai-consent`, { auth: true, method: "PUT", body: { ai_consent: !k.ai_consent } });
    reload();
  }
  async function resetPin() {
    try {
      await api(`/children/${k.id}/reset-pin`, { auth: true, body: { pin } });
      setPin(""); setMsg("PIN을 바꿨어요. 잠금이 풀렸어요."); setOpen("");
      reload();
    } catch (e) { setMsg(err(e)); }
  }
  async function remove() {
    if (!confirm(`${k.nickname} 프로필과 학습 기록·대화를 영구 삭제할까요?`)) return;
    await api(`/children/${k.id}`, { auth: true, method: "DELETE" });
    reload();
  }
  return (
    <section className="card" data-testid="child-card">
      <strong>{k.nickname}</strong> · {bodyName} {k.target[1] ? gradeLabel(k.target[1]) : ""}
      {k.locked && <span className="badge" data-testid={`locked-${k.nickname}`}> 🔒 PIN 잠김</span>}
      <div>
        <button data-testid={`enter-${k.nickname}`} onClick={onEnter}>공부 시작</button>
        <button className="secondary" data-testid={`plan-${k.nickname}`} onClick={() => setOpen(open === "plan" ? "" : "plan")}>학습 계획</button>
        <button className="secondary" data-testid={`time-${k.nickname}`} onClick={() => setOpen(open === "time" ? "" : "time")}>사용 시간</button>
        <button className="secondary" data-testid={`pin-${k.nickname}`} onClick={() => setOpen(open === "pin" ? "" : "pin")}>PIN 재설정</button>
      </div>
      <label className="check"><input type="checkbox" data-testid={`consent-${k.nickname}`} checked={k.ai_consent} onChange={toggleConsent} />AI 처리 동의 (끄면 챗봇만 꺼져요)</label>
      {open === "pin" && (
        <div>
          <input data-testid={`newpin-${k.nickname}`} type="password" inputMode="numeric" placeholder="새 PIN (숫자 4~6자리)" value={pin} onChange={(e) => setPin(e.target.value)} />
          <button data-testid={`resetpin-${k.nickname}`} onClick={resetPin}>PIN 바꾸고 잠금 풀기</button>
        </div>
      )}
      {open === "plan" && <PlanEditor childId={k.id} />}
      {open === "time" && <ScreenTimeEditor childId={k.id} />}
      {msg && <p data-testid={`child-msg-${k.nickname}`}>{msg}</p>}
      <button className="secondary danger" data-testid={`delete-${k.nickname}`} onClick={remove}>프로필 삭제</button>
    </section>
  );
}

function PlanEditor({ childId }: { childId: string }) {
  const [plan, setPlan] = useState<any>(null);
  const [date, setDate] = useState("");
  const [count, setCount] = useState("");
  const [msg, setMsg] = useState("");
  useEffect(() => {
    api(`/children/${childId}/plan`, { auth: true }).then((p) => { setPlan(p); setDate(p.exam_date ?? ""); setCount(p.daily_count?.toString() ?? ""); }).catch((e) => setMsg(err(e)));
  }, [childId]);
  async function save() {
    setMsg("");
    try {
      setPlan(await api(`/children/${childId}/plan`, { auth: true, method: "PUT", body: { exam_date: date || null, daily_count: count ? Number(count) : null } }));
      setMsg("저장했어요.");
    } catch (e) { setMsg(err(e)); }
  }
  return (
    <div className="sub" data-testid="plan-editor">
      <label>시험일 (비우면 90일 계획)<input type="date" data-testid="plan-date" value={date} onChange={(e) => setDate(e.target.value)} /></label>
      <label>하루 새 한자 수 (3~15, 비우면 자동 균등 분배)<input type="number" min={3} max={15} data-testid="plan-count" value={count} onChange={(e) => setCount(e.target.value)} /></label>
      <button data-testid="plan-save" onClick={save}>계획 저장</button>
      {plan && <p data-testid="plan-summary">오늘 새 한자 {plan.today_quota}자 · 남은 한자 {plan.remaining}자 · 남은 날 {plan.days_left}일{plan.feasible ? "" : " (하루 15자를 넘어 시험일까지 다 배우기 어려워요)"}</p>}
      {msg && <p data-testid="plan-msg">{msg}</p>}
    </div>
  );
}

function ScreenTimeEditor({ childId }: { childId: string }) {
  const [daily, setDaily] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [msg, setMsg] = useState("");
  useEffect(() => {
    api(`/children/${childId}/screentime`, { auth: true }).then((s) => {
      setDaily(s.daily_minutes?.toString() ?? "");
      if (s.windows[0]) { setFrom(s.windows[0][0]); setTo(s.windows[0][1]); }
    });
  }, [childId]);
  async function save() {
    setMsg("");
    try {
      await api(`/children/${childId}/screentime`, { auth: true, method: "PUT", body: { daily_minutes: daily ? Number(daily) : null, windows: from && to ? [[from, to]] : [] } });
      setMsg("저장했어요.");
    } catch (e) { setMsg(err(e)); }
  }
  return (
    <div className="sub" data-testid="screentime-editor">
      <label>하루 총 사용 시간(분, 비우면 제한 없음)<input type="number" min={1} data-testid="st-daily" value={daily} onChange={(e) => setDaily(e.target.value)} /></label>
      <label>사용 가능 시간대 시작<input type="time" data-testid="st-from" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
      <label>사용 가능 시간대 끝<input type="time" data-testid="st-to" value={to} onChange={(e) => setTo(e.target.value)} /></label>
      <button data-testid="st-save" onClick={save}>사용 시간 저장</button>
      {msg && <p data-testid="st-msg">{msg}</p>}
    </div>
  );
}

// ---------------------------------------------------------------- reports + conversations + safety (AC8, AC12)
export function Reports({ kids }: { kids: Child[] }) {
  const [sel, setSel] = useState(kids[0]?.id ?? "");
  const k = kids.find((x) => x.id === sel) ?? kids[0];
  const [basic, setBasic] = useState<any>(null);
  const [detail, setDetail] = useState<any>(null);
  const [msg, setMsg] = useState("");
  useEffect(() => {
    setDetail(null); setMsg("");
    if (k) api(`/children/${k.id}/report/basic`, { auth: true }).then(setBasic).catch(() => undefined);
  }, [k?.id]);
  if (!k) return <p>자녀를 먼저 만들어 주세요.</p>;
  async function loadDetail() {
    setMsg("");
    try { setDetail(await api(`/children/${k.id}/report/detailed`, { auth: true })); }
    catch (e) { setMsg(e instanceof ApiError && e.status === 402 ? "상세 리포트는 구독 전용이에요." : "오류가 발생했어요."); }
  }
  return (
    <>
      {kids.length > 1 && (
        <select data-testid="report-child" value={k.id} onChange={(e) => setSel(e.target.value)}>
          {kids.map((x) => <option key={x.id} value={x.id}>{x.nickname}</option>)}
        </select>
      )}
      <section className="card" aria-label="리포트">
        <h2>{k.nickname} 기본 리포트 (무료)</h2>
        {basic && (
          <ul data-testid="basic-report">
            <li>학습 시간: {basic.study_minutes_total}분</li>
            <li>오늘의 한자 완료율: {pct(basic.todays_hanja.completion_rate)}</li>
            <li>복습 정답률: {pct(basic.review.accuracy)}</li>
          </ul>
        )}
        <button data-testid="detail-report" onClick={loadDetail}>상세 리포트 보기</button>
        {msg && <p role="alert" data-testid="detail-msg">{msg}</p>}
        {detail && (
          <div data-testid="detail-body">
            <p>취약 한자: {detail.weak_hanja.slice(0, 10).map((w: any) => w.char).join(" ") || "-"}</p>
            <p>모의고사 점수 추이: {detail.mock_exam_trend.map((t: any) => t.score).join(" → ") || "-"}</p>
            <p>합격 가능성: {detail.pass_likelihood ? (detail.pass_likelihood.likely_pass ? "높음" : "노력 필요") + ` (최근 평균 ${detail.pass_likelihood.average_score.toFixed(1)}점 / 합격선 ${detail.pass_likelihood.pass_score}점)` : "-"}</p>
            <p>쓰기 완료 한자: {detail.writing_completed_count}자</p>
          </div>
        )}
      </section>
      <Conversations child={k} />
      <SafetyEvents child={k} />
    </>
  );
}

function Conversations({ child }: { child: Child }) {
  const [threads, setThreads] = useState<any[]>([]);
  const [open, setOpen] = useState<{ id: string; messages: any[] } | null>(null);
  const load = useCallback(() => api(`/children/${child.id}/conversations`, { auth: true }).then(setThreads).catch(() => undefined), [child.id]);
  useEffect(() => { load(); setOpen(null); }, [load]);
  async function view(id: string) {
    const r = await api(`/children/${child.id}/conversations/${id}`, { auth: true });
    setOpen({ id, messages: r.messages });
  }
  async function del(id: string) {
    await api(`/children/${child.id}/conversations/${id}`, { auth: true, method: "DELETE" });
    if (open?.id === id) setOpen(null);
    load();
  }
  return (
    <section className="card" aria-label="대화 열람" data-testid="parent-conversations">
      <h2>대화 열람 (무료)</h2>
      {threads.length === 0 && <p>저장된 대화가 없어요.</p>}
      {threads.map((t) => (
        <div key={t.thread_id}>
          <button className="secondary" data-testid="pc-thread" onClick={() => view(t.thread_id)}>{t.title || "(제목 없음)"} · {t.last_message_at.slice(0, 16).replace("T", " ")}</button>
          <button className="secondary" data-testid="pc-delete" onClick={() => del(t.thread_id)}>삭제</button>
        </div>
      ))}
      {open && <div data-testid="pc-messages">{open.messages.map((m, i) => <div key={i} className={`msg ${m.role === "user" ? "user" : "bot"}`}>{m.content}</div>)}</div>}
    </section>
  );
}

const CATEGORY: Record<string, string> = { pii: "개인정보", profanity: "욕설", violence: "폭력·위험", sexual: "성적 내용", self_harm: "자해", drugs: "약물", harmful: "유해" };

function SafetyEvents({ child }: { child: Child }) {
  const [events, setEvents] = useState<any[]>([]);
  useEffect(() => { api(`/children/${child.id}/safety-events`, { auth: true }).then(setEvents).catch(() => undefined); }, [child.id]);
  return (
    <section className="card" aria-label="안전 알림" data-testid="safety-events">
      <h2>차단된 입력 기록 (원문은 저장하지 않아요)</h2>
      {events.length === 0 && <p>차단된 입력이 없어요.</p>}
      <ul>{events.map((e) => <li key={e.id} data-testid="safety-event">{e.at.slice(0, 16).replace("T", " ")} · {CATEGORY[e.category] ?? e.category} · 같은 대화 {e.thread_count}회째{e.thread_count >= 3 ? " (오늘 챗봇 잠김)" : ""}</li>)}</ul>
    </section>
  );
}

// ---------------------------------------------------------------- alerts: push inbox, extension requests, parent PIN (AC11, AC12)
const KIND: Record<string, string> = {
  safety_event: "유해·개인정보 입력이 차단됐어요", extension_request: "사용 시간 연장 요청", weekly_summary: "주간 학습 요약",
  usage_limit_reached: "무료 한도 도달", dormancy_notice: "휴면 전환 예정 안내", payment_failed: "결제 실패",
};

export function Alerts() {
  const [reqs, setReqs] = useState<any[]>([]);
  const [notes, setNotes] = useState<any[]>([]);
  const [pin, setPin] = useState("");
  const [msg, setMsg] = useState("");
  const load = useCallback(async () => {
    setReqs(await api("/parents/me/extension-requests", { auth: true }).catch(() => []));
    const n = await api("/parents/me/notifications", { auth: true }).catch(() => []);
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
    await api(`/parents/me/extension-requests/${id}`, { auth: true, body: { approve } });
    load();
  }
  async function savePin() {
    try { await api("/parents/me/pin", { auth: true, method: "PUT", body: { pin } }); setPin(""); setMsg("부모 PIN을 저장했어요."); }
    catch (e) { setMsg(err(e)); }
  }
  return (
    <>
      <section className="card" aria-label="연장 요청">
        <h2>사용 시간 연장 요청</h2>
        {reqs.length === 0 && <p>대기 중인 요청이 없어요.</p>}
        {reqs.map((r) => (
          <div key={r.id} data-testid="ext-request">
            {r.nickname}: {r.minutes}분 더 쓰고 싶어요
            <button data-testid="ext-approve" onClick={() => resolve(r.id, true)}>허락</button>
            <button className="secondary" data-testid="ext-reject" onClick={() => resolve(r.id, false)}>거절</button>
          </div>
        ))}
      </section>
      <section className="card" aria-label="부모 PIN">
        <h2>부모 PIN (아이 기기에서 시간 추가할 때)</h2>
        <input type="password" inputMode="numeric" data-testid="parent-pin" value={pin} onChange={(e) => setPin(e.target.value)} />
        <button data-testid="parent-pin-save" onClick={savePin}>저장</button>
        <button className="secondary" onClick={() => typeof Notification !== "undefined" && Notification.requestPermission()}>기기 알림 켜기</button>
        {msg && <p data-testid="parent-pin-msg">{msg}</p>}
      </section>
      <section className="card" aria-label="알림">
        <h2>알림</h2>
        <ul>{[...notes].reverse().map((n) => <li key={n.seq} data-testid={`note-${n.kind}`}>{KIND[n.kind] ?? n.kind}{n.payload?.message ? ` — ${n.payload.message}` : ""}{n.payload?.nickname ? ` (${n.payload.nickname})` : ""}</li>)}</ul>
      </section>
    </>
  );
}

async function showSystemNotifications(items: any[]) {
  try {
    if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
    const reg = await navigator.serviceWorker?.ready;
    for (const n of items) await reg?.showNotification("한자 튜터", { body: KIND[n.kind] ?? n.kind, icon: "/icon-192.png", tag: `n${n.seq}` });
  } catch { /* notifications are best-effort */ }
}

// ---------------------------------------------------------------- account deletion (AC3)
export function Account({ onDeleted }: { onDeleted: () => void }) {
  const [msg, setMsg] = useState("");
  async function del() {
    if (!confirm("계정을 삭제하면 모든 자녀의 학습 기록과 대화가 즉시 삭제돼요. 결제 기록은 법에 따라 5년간 따로 보관돼요. 삭제할까요?")) return;
    try { await api("/parents/me", { auth: true, method: "DELETE" }); onDeleted(); }
    catch (e) { setMsg(err(e)); }
  }
  return (
    <section className="card" aria-label="계정">
      <h2>계정</h2>
      <p>1년 동안 접속하지 않으면 30일 전에 안내한 뒤 휴면 계정으로 바뀌어요. 다시 로그인(본인인증)하면 복구돼요.</p>
      <button className="danger" data-testid="delete-account" onClick={del}>계정 삭제</button>
      {msg && <p className="err">{msg}</p>}
    </section>
  );
}
