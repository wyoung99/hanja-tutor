"use client";
import { ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, childLockMessage, mmss, passLine, pct, scoreLine, streamChat, TYPE_LABEL } from "../lib/api";
import type { Pt } from "../lib/strokes";
import StrokePad from "./StrokePad";
import { Alert, Badge, Bubble, Button, Card, CardTitle, cx, Field, inputClass, Muted, ProgressBar } from "./ui";

export type Child = { id: string; nickname: string; ai_consent: boolean; locked?: boolean; target: string[] };
export type Card = { char: string; hun: string | null; eum: string | null; reviewed: boolean; strokes: number; tracing: "graded" | "animation_only" | "unavailable" };

/** 훈·음 of the child's selected exam body (empty source rows await human review). */
export function reading(c: Card) {
  return c.hun && c.eum ? `${c.hun} ${c.eum}` : "훈·음 검수 중";
}

// ---------------------------------------------------------------- screen time (AC11)
type StStatus = { locked: boolean; warning: boolean; lock_pending: boolean; reason: string | null; seconds_left: number | null };
export const HEARTBEAT_MS = 15_000;

/** Sends heartbeats while the child app is open; shows the 5-minute notice and the lock screen. */
export function ScreenGuard({ child, children }: { child: Child; children: ReactNode }) {
  const [st, setSt] = useState<StStatus | null>(null);
  const beat = useCallback(() => api<StStatus>(`/children/${child.id}/heartbeat`, { body: {} }).then(setSt).catch(() => undefined), [child.id]);
  useEffect(() => {
    beat();
    const t = setInterval(beat, HEARTBEAT_MS);
    return () => clearInterval(t);
  }, [beat]);
  if (st?.locked) return <LockScreen child={child} reason={st.reason} onUnlocked={beat} />;
  return (
    <>
      {st?.warning && <Alert tone="warning" className="mb-4 text-base font-semibold" role="status" data-testid="time-warning">⏰ 곧 쉬는 시간이에요! 약 {Math.ceil((st.seconds_left ?? 0) / 60)}분 남았어요.</Alert>}
      {st?.lock_pending && <Alert tone="warning" className="mb-4 text-base font-semibold" data-testid="lock-pending">사용 시간이 끝났어요. 모의고사를 마치면 쉬어요.</Alert>}
      {children}
    </>
  );
}

function LockScreen({ child, reason, onUnlocked }: { child: Child; reason: string | null; onUnlocked: () => void }) {
  const [req, setReq] = useState<{ id: string; status: string } | null>(null);
  const [password, setPassword] = useState("");
  const [minutes, setMinutes] = useState(15);
  const [msg, setMsg] = useState("");
  useEffect(() => {
    if (!req || req.status !== "pending") return;
    const t = setInterval(async () => {
      const r = await api(`/children/${child.id}/extension-requests/${req.id}`).catch(() => null);
      if (r && r.status !== "pending") {
        setReq(r);
        if (r.status === "approved") onUnlocked();
        else setMsg("부모님이 오늘은 여기까지래요. 내일 또 만나요!");
      }
    }, 3000);
    return () => clearInterval(t);
  }, [req, child.id, onUnlocked]);
  async function ask() {
    setReq(await api(`/children/${child.id}/extension-requests`, { body: { minutes } }));
    setMsg("부모님께 물어봤어요. 잠시 기다려 주세요.");
  }
  async function grant() {
    try {
      await api(`/children/${child.id}/screentime/extra`, { body: { parent_password: password, minutes } });
      setPassword("");
      onUnlocked();
    } catch (e) {
      setMsg(e instanceof ApiError && e.detail?.code === "parent_password_locked"
        ? "비밀번호를 여러 번 틀렸어요. 부모님이 부모 계정으로 로그인하면 다시 쓸 수 있어요."
        : "부모님 비밀번호가 달라요.");
    }
  }
  return (
    <Card data-testid="app-locked" className="mx-auto max-w-lg text-center">
      <div aria-hidden="true" className="mx-auto mb-3 flex size-20 items-center justify-center rounded-full bg-warning-100 text-4xl dark:bg-warning-900/50">🌙</div>
      <h2 className="text-2xl font-extrabold tracking-tight">{reason === "outside_window" ? "지금은 쉬는 시간이에요" : "오늘 사용 시간이 끝났어요"}</h2>
      <div className="mt-6 space-y-3 text-left">
        <Field label="더 필요한 시간(분)">
          <select className={inputClass} data-testid="extra-minutes" value={minutes} onChange={(e) => setMinutes(Number(e.target.value))}>
            {[5, 10, 15, 30].map((m) => <option key={m} value={m}>{m}분</option>)}
          </select>
        </Field>
        <Button className="w-full" size="lg" data-testid="ask-extension" disabled={req?.status === "pending"} onClick={ask}>부모님께 시간 더 달라고 하기</Button>
      </div>
      <div className="mt-6 space-y-3 rounded-2xl border border-line bg-soft/60 p-4 text-left">
        <h3 className="font-bold">부모님이 옆에 계신가요?</h3>
        <input className={inputClass} data-testid="parent-password-extra" type="password" autoComplete="off" placeholder="부모님 계정 비밀번호" value={password} onChange={(e) => setPassword(e.target.value)} />
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" data-testid="grant-extra" onClick={grant}>부모님 비밀번호로 시간 추가</Button>
          <Button variant="ghost" onClick={onUnlocked}>다시 확인</Button>
        </div>
      </div>
      {msg && <Alert tone="info" className="mt-4 text-left text-base" data-testid="lock-msg">{msg}</Alert>}
    </Card>
  );
}

// ---------------------------------------------------------------- tracing (AC7)
export function Tracing({ child, card, onDone }: { child: Child; card: Card; onDone: (ok: boolean) => void }) {
  const [mode, setMode] = useState<string>("");
  const [guide, setGuide] = useState<Pt[][]>([]);
  const [answer, setAnswer] = useState<Pt[] | null>(null);
  const [wrong, setWrong] = useState<Pt[] | null>(null);
  const [accepted, setAccepted] = useState<Pt[][]>([]);
  const [msg, setMsg] = useState("획순대로 한 획씩 써 보세요.");
  const [result, setResult] = useState("");
  useEffect(() => {
    api(`/children/${child.id}/tracing/${card.char}/start`, { body: {} }).then((r) => { setMode(r.mode); setGuide(r.strokes ?? []); });
  }, []);
  async function stroke(s: Pt[]) {
    const r = await api(`/children/${child.id}/tracing/${card.char}/stroke`, { body: { stroke: s } });
    if (r.ok) {
      setAccepted((a) => [...a, s]);
      setWrong(null);
      setAnswer(null);
    } else {
      setWrong(r.highlight_stroke ? s : null); // the wrong stroke turns red right away
      if (r.reveal_answer) setAnswer(r.answer_stroke);
    }
    if (r.done) { setResult(r.result); setMsg(r.result === "pass" ? "통과! 잘 썼어요 🎉" : "재시도 — 정답 획을 봐서 다음에 다시 도전해요."); }
    else if (r.ok) setMsg("좋아요! 다음 획이에요.");
    else setMsg(r.reveal_answer ? "초록 획이 정답이에요. 따라 써 볼까요?" : `빨간 획이 틀렸어요(${errorText(r.error)}). 다시 써 볼까요?`);
  }
  // feedback colour only (derived from the existing state)
  const tone = result ? (result === "pass" ? "success" : "warning") : wrong ? "danger" : answer ? "success" : "info";
  return (
    <Card aria-label="따라쓰기" className="mx-auto max-w-xl">
      <div className="mb-4 flex items-center gap-4">
        <div lang="zh-Hant" className="flex size-20 shrink-0 items-center justify-center rounded-2xl bg-brand-50 font-hanja text-6xl font-bold text-brand-900 dark:bg-brand-950/60 dark:text-brand-100">{card.char}</div>
        <div>
          <p className="text-sm font-semibold text-muted">따라쓰기</p>
          <p className="text-xl font-bold">{reading(card)}</p>
        </div>
      </div>
      {mode === "unavailable" && <Alert tone="warning" data-testid="tracing-unavailable">이 글자는 획순 데이터가 없어서 따라쓰기를 할 수 없어요.</Alert>}
      {mode === "animation_only" && <Alert tone="info" className="mb-3" data-testid="animation-only">이 글자는 획순만 볼 수 있어요(채점 제외).</Alert>}
      {mode === "graded" && <StrokePad onStroke={stroke} disabled={!!result} guide={guide} highlight={answer} wrong={wrong} accepted={accepted} />}
      {mode === "animation_only" && <StrokePad onStroke={() => undefined} disabled guide={guide} />}
      <Alert tone={tone} role="status" aria-live="polite" className="mt-4 text-base font-semibold" data-testid="trace-msg">{msg}</Alert>
      {result && (
        <p className="mt-3 text-center">
          <Badge tone={result === "pass" ? "success" : "warning"} className="px-4 py-1.5 text-base" data-testid="trace-result">{result === "pass" ? "통과" : "재시도"}</Badge>
        </p>
      )}
      <div className="mt-4 flex flex-wrap justify-center gap-2">
        {result && <Button size="lg" data-testid="trace-finish" onClick={() => onDone(result === "pass")}>끝내기</Button>}
        <Button size="lg" variant="secondary" onClick={() => onDone(false)}>뒤로</Button>
      </div>
    </Card>
  );
}

function errorText(e: string | null) {
  return { order: "순서", direction: "방향", position: "위치", length: "길이" }[e ?? ""] ?? "획";
}

// ---------------------------------------------------------------- mock exam (AC6)
export const EXAM_SYNC_MS = 5_000;

export function Exam({ child }: { child: Child }) {
  const [exam, setExam] = useState<any>(null);
  const [answers, setAnswers] = useState<Record<string, unknown>>({});
  const [result, setResult] = useState<any>(null);
  const [msg, setMsg] = useState("");
  const [left, setLeft] = useState(0);
  const [strokes, setStrokes] = useState<Record<string, Pt[][]>>({});
  const deadline = useRef(0);

  const adopt = useCallback((v: any) => {
    if (v.submitted && v.result) { setResult(v.result); setExam(null); return; }
    setExam(v);
    setAnswers(v.answers ?? {});
    deadline.current = Date.now() + v.remaining_seconds * 1000;
    setLeft(v.remaining_seconds);
  }, []);

  // leaving and coming back resumes the running exam (the server timer never stops)
  useEffect(() => {
    api(`/children/${child.id}/exams/active`).then(adopt).catch(() => undefined);
  }, [child.id, adopt]);

  useEffect(() => {
    if (!exam) return;
    const tick = setInterval(() => {
      const s = Math.max(0, Math.round((deadline.current - Date.now()) / 1000));
      setLeft(s);
      if (s === 0) api(`/exams/${exam.exam_id}`).then(adopt).catch(() => undefined); // server auto-submits
    }, 1000);
    const sync = setInterval(() => api(`/exams/${exam.exam_id}`).then(adopt).catch(() => undefined), EXAM_SYNC_MS);
    return () => { clearInterval(tick); clearInterval(sync); };
  }, [exam?.exam_id, adopt]);

  async function start() {
    setMsg("");
    try { adopt(await api(`/children/${child.id}/exams`, { body: {} })); }
    catch (e) {
      if (e instanceof ApiError && e.status === 429) setMsg(childLockMessage("mockexam"));
      else if (e instanceof ApiError && e.detail?.code === "exam_format_pending") setMsg("이 급수의 모의고사는 형식 준비 중이에요. 오늘의 한자와 따라쓰기로 공부해요!");
      else setMsg("시작할 수 없어요.");
    }
  }
  async function answer(qid: string, value: unknown) {
    setAnswers((a) => ({ ...a, [qid]: value }));
    await api(`/exams/${exam.exam_id}/answer`, { body: { question_id: qid, value } }).catch(() => api(`/exams/${exam.exam_id}`).then(adopt));
  }
  async function submit() { setResult(await api(`/exams/${exam.exam_id}/submit`, { body: {} })); setExam(null); }

  if (result) return <ExamResult result={result} onClose={() => setResult(null)} />;
  if (!exam)
    return (
      <Card className="mx-auto max-w-xl text-center">
        <div aria-hidden="true" className="mx-auto mb-3 flex size-20 items-center justify-center rounded-3xl bg-brand-100 text-4xl dark:bg-brand-900/60">📝</div>
        <h2 className="text-2xl font-extrabold tracking-tight">모의고사 (실전형)</h2>
        <Muted className="mt-2">실제 시험과 같은 문항 수와 시간으로 풀어 봐요. 중간에 나가도 이어서 볼 수 있어요.</Muted>
        <Button size="lg" className="mt-6 w-full sm:w-auto sm:px-10" data-testid="exam-start" onClick={start}>시작하기</Button>
        {msg && <Alert tone="warning" className="mt-4 text-left text-base" data-testid="exam-msg">{msg}</Alert>}
      </Card>
    );
  const answered = exam.questions.filter((q: any) => {
    const a = answers[q.id];
    return a != null && !(Array.isArray(a) && a.length === 0);
  }).length;
  const timerTone = left <= 60 ? "bg-danger-600 text-white dark:bg-danger-500" : left <= 300 ? "bg-warning-500 text-warning-950" : "bg-brand-700 text-white dark:bg-brand-400 dark:text-brand-950";
  return (
    <section aria-label="모의고사" data-testid="exam-running" data-exam-id={exam.exam_id} className="space-y-4">
      <div className="sticky top-18 z-10 flex flex-wrap items-center gap-3 rounded-2xl border border-line bg-card/95 p-3 shadow-(--shadow-card) backdrop-blur">
        <p className={cx("inline-flex items-center gap-2 rounded-full px-4 py-1.5 text-lg font-bold tabular-nums", timerTone)} data-testid="exam-timer" aria-live="polite">
          <svg aria-hidden="true" viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round"><circle cx="12" cy="13" r="8" /><path d="M12 9v4l2 2M9 2h6" /></svg>
          남은 시간 {mmss(left)}
        </p>
        <div className="min-w-32 flex-1">
          <div className="mb-1 flex justify-between text-xs font-semibold text-muted"><span>푼 문항</span><span>{answered} / {exam.questions.length}</span></div>
          <ProgressBar value={answered / Math.max(1, exam.questions.length)} label="푼 문항 수" />
        </div>
      </div>
      <p className="text-sm text-muted" data-testid="exam-format">제한 시간 {exam.minutes}분 · 합격 {passLine({ ...exam, question_count: exam.questions.length })} · 문항 {exam.questions.length}개</p>
      {exam.questions.map((q: any, i: number) => (
        <Card key={q.id} data-testid="exam-question" data-type={q.type} className="p-4 sm:p-5">
          <p className="mb-4 flex gap-3 text-lg font-semibold leading-snug">
            <span aria-hidden="true" className="inline-flex size-8 shrink-0 items-center justify-center rounded-full bg-brand-100 text-sm font-bold text-brand-800 dark:bg-brand-900 dark:text-brand-200">{i + 1}</span>
            <span><span className="sr-only">{i + 1}. </span>{q.prompt}</span>
          </p>
          {q.figure && <div className="mb-4 flex justify-center"><StrokeFigure strokes={q.figure.strokes} highlight={q.figure.highlight} /></div>}
          {q.type === "writing" ? (
            <div className="flex flex-col items-center gap-2">
              <StrokePad onStroke={(s) => {
                const next = [...(strokes[q.id] ?? []), s];
                setStrokes((m) => ({ ...m, [q.id]: next }));
                answer(q.id, next);
              }} accepted={strokes[q.id] ?? []} />
              <div className="flex items-center gap-3">
                <small className="text-sm text-muted">{(strokes[q.id] ?? []).length}획 입력됨</small>
                <Button variant="secondary" size="sm" onClick={() => { setStrokes((m) => ({ ...m, [q.id]: [] })); answer(q.id, []); }}>지우기</Button>
              </div>
            </div>
          ) : (
            <div className="grid gap-2 sm:grid-cols-2">
              {q.options.map((o: string) => (
                <label key={o} className={cx("flex min-h-12 cursor-pointer items-center gap-3 rounded-xl border-2 px-4 py-2.5 text-lg font-medium transition-colors",
                  "border-line bg-card hover:border-brand-300 hover:bg-brand-50 dark:hover:bg-brand-950/50",
                  "has-checked:border-brand-600 has-checked:bg-brand-50 has-checked:text-brand-900 dark:has-checked:border-brand-400 dark:has-checked:bg-brand-950/70 dark:has-checked:text-brand-100")}>
                  <input type="radio" className="size-5 shrink-0 accent-brand-700 dark:accent-brand-400" name={q.id} checked={answers[q.id] === o} onChange={() => answer(q.id, o)} />
                  <span>{o}</span>
                </label>
              ))}
            </div>
          )}
        </Card>
      ))}
      <Button size="lg" className="w-full" data-testid="exam-submit" onClick={submit}>제출하기</Button>
    </section>
  );
}

/** 筆順 figure: the char's stroke medians (hanzi-writer data) with the asked stroke in red. */
export function StrokeFigure({ strokes, highlight }: { strokes: [number, number][][]; highlight: number }) {
  const path = (s: [number, number][]) => s.map(([x, y], i) => `${i ? "L" : "M"}${x} ${y}`).join(" ");
  return (
    <svg viewBox="0 0 1000 1000" width={160} height={160} role="img" aria-label="빨간색 획을 보세요" data-testid="stroke-figure"
      className="rounded-xl border-2 border-line bg-white">
      {strokes.map((s, i) => (
        <path key={i} d={path(s)} fill="none" strokeLinecap="round" strokeLinejoin="round"
          stroke={i === highlight ? "#e0533d" : "#9aa59f"} strokeWidth={i === highlight ? 70 : 50} />
      ))}
    </svg>
  );
}

function ExamResult({ result, onClose }: { result: any; onClose: () => void }) {
  return (
    <Card data-testid="exam-result" className="space-y-6">
      <div className={cx("rounded-2xl p-6 text-center",
        result.passed ? "bg-success-50 dark:bg-success-950/50" : "bg-warning-50 dark:bg-warning-950/40")}>
        <h2 className="text-sm font-bold uppercase tracking-wider text-muted">결과</h2>
        {result.auto_submitted && <p className="mt-1 text-sm text-muted" data-testid="exam-auto">시간이 끝나서 자동으로 제출했어요.</p>}
        <p className="mt-2 text-3xl font-extrabold tracking-tight sm:text-4xl" data-testid="exam-score">
          {scoreLine(result)} <span className="block text-base font-semibold text-muted sm:inline">(합격선 {passLine(result)})</span>
        </p>
        <p className="mt-4">
          <Badge tone={result.passed ? "success" : "danger"} className="px-5 py-2 text-lg" data-testid="exam-pass">{result.passed ? "합격 🎉" : "불합격 — 다시 도전해요"}</Badge>
        </p>
      </div>
      {result.notice && <Alert tone="info" data-testid="exam-notice">ℹ️ {result.notice}</Alert>}
      <div>
        <h3 className="mb-3 text-lg font-bold">유형별 정답률</h3>
        <div data-testid="exam-by-type" className="space-y-3">
          {Object.entries(result.by_type ?? {}).map(([t, v]: [string, any]) => (
            <div key={t} data-substituted={v.substituted ? "1" : "0"}>
              <div className="mb-1 flex flex-wrap items-baseline justify-between gap-x-3 text-sm">
                <span className="font-semibold">{v.label ?? TYPE_LABEL[t] ?? t}{v.substituted && <small className="font-normal text-muted"> (대체: {TYPE_LABEL[v.type] ?? v.type})</small>}</span>
                <span className="tabular-nums text-muted">{v.correct}/{v.total} · <b className="text-ink">{pct(v.rate)}</b></span>
              </div>
              <ProgressBar value={v.rate} tone={v.rate == null ? "neutral" : v.rate >= 0.7 ? "success" : v.rate >= 0.4 ? "warning" : "danger"} label={`${v.label ?? TYPE_LABEL[t] ?? t} 정답률`} />
            </div>
          ))}
        </div>
      </div>
      <div>
        <h3 className="mb-3 text-lg font-bold">틀린 문항 해설 ({result.wrong.length})</h3>
        <ol data-testid="exam-wrong" className="space-y-2">
          {result.wrong.map((w: any) => (
            <li key={w.id} className="rounded-xl border border-line bg-soft/60 p-3 text-[15px]">
              <div className="font-semibold">{w.prompt}</div>
              <div className="mt-1">내 답: <span className="text-danger-700 dark:text-danger-300">{w.your_answer == null ? "(안 씀)" : String(w.your_answer)}</span> · 정답: <b className="text-success-700 dark:text-success-300">{w.answer}</b></div>
              <div className="mt-1 text-sm text-muted">{w.explanation}</div>
            </li>
          ))}
        </ol>
      </div>
      <Alert tone="brand">틀린 한자는 복습 목록에 들어갔어요.</Alert>
      <Button variant="secondary" size="lg" className="w-full sm:w-auto" onClick={onClose}>닫기</Button>
    </Card>
  );
}

// ---------------------------------------------------------------- chatbot (AC8-AC10)
type Msg = { role: "user" | "bot"; text: string };
type Thread = { thread_id: string; title: string; last_message_at: string };

export function Chat({ child }: { child: Child }) {
  const [threads, setThreads] = useState<Thread[]>([]);
  const [tid, setTid] = useState<string | null>(null);
  const [log, setLog] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [locked, setLocked] = useState(false);
  const [failed, setFailed] = useState<{ message: string; kind: "timeout" | "error" } | null>(null);
  const [notice, setNotice] = useState("");
  const loadThreads = useCallback(() => api<Thread[]>(`/children/${child.id}/threads`).then(setThreads).catch(() => undefined), [child.id]);
  useEffect(() => { loadThreads(); }, [loadThreads]);
  // keep the newest message in view inside the scrollable log
  const logRef = useRef<HTMLDivElement>(null);
  useEffect(() => { const el = logRef.current; if (el) el.scrollTop = el.scrollHeight; }, [log, failed]);

  async function open(id: string) {
    setTid(id);
    setFailed(null);
    const r = await api(`/children/${child.id}/threads/${id}`);
    setLog(r.messages.map((m: any) => ({ role: m.role === "user" ? "user" : "bot", text: m.content })));
  }
  function fresh() { setTid(null); setLog([]); setFailed(null); }
  async function remove(id: string) {
    await api(`/children/${child.id}/threads/${id}`, { method: "DELETE" });
    if (id === tid) fresh();
    loadThreads();
  }

  async function ask(m: string) {
    setBusy(true);
    setFailed(null);
    setNotice("");
    setLog((l) => [...l, { role: "bot", text: "" }]);
    const setBot = (t: string) => setLog((l) => [...l.slice(0, -1), { role: "bot", text: t }]);
    let streamed = "";
    try {
      const outcome = await streamChat(`/children/${child.id}/chat/stream`, { message: m, thread_id: tid }, (e) => {
        if (e.type === "thread") setTid(e.thread_id);
        else if (e.type === "token") { streamed += e.text; setBot(streamed); }
        else if (e.type === "done") {
          setBot(e.status === "locked" ? childLockMessage("chatbot") : e.reply);
          if (e.status === "locked" || e.locked_today) setLocked(true);
        }
      });
      if (outcome !== "done") {
        setLog((l) => l.slice(0, -1));
        setFailed({ message: m, kind: outcome === "timeout" ? "timeout" : "error" });
      }
    } catch (e) {
      setLog((l) => l.slice(0, -1));
      if (e instanceof ApiError && e.status === 403) setNotice("부모님이 허락하면 쓸 수 있어요.");
      else if (e instanceof ApiError && e.status === 423) setNotice("지금은 쉬는 시간이에요.");
      else setFailed({ message: m, kind: "error" });
    } finally {
      setBusy(false);
      loadThreads();
    }
  }
  function send() {
    const m = text.trim();
    if (!m || busy) return;
    setText("");
    setLog((l) => [...l, { role: "user", text: m }]);
    ask(m);
  }

  if (!child.ai_consent)
    return (
      <Card className="text-center">
        <CardTitle>한자 친구</CardTitle>
        <Alert tone="info" className="text-base" data-testid="chat-disabled">부모님이 허락하면 쓸 수 있어요.</Alert>
      </Card>
    );
  return (
    <Card aria-label="한자 친구" className="flex flex-col gap-4 p-0 sm:p-0">
      <div className="flex flex-col gap-3 border-b border-line px-4 pt-4 pb-3 sm:px-6 sm:pt-5">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-lg font-bold">한자 친구</h2>
          <Button variant="secondary" size="sm" data-testid="new-thread" onClick={fresh}>＋ 새 대화</Button>
        </div>
        {threads.length > 0 && (
          <div className="no-scrollbar -mx-1 flex gap-2 overflow-x-auto px-1 pb-1">
            {threads.map((t) => {
              const on = t.thread_id === tid;
              return (
                <span key={t.thread_id} className={cx("inline-flex shrink-0 items-center overflow-hidden rounded-full border text-sm",
                  on ? "border-brand-600 bg-brand-700 text-white dark:border-brand-400 dark:bg-brand-400 dark:text-brand-950" : "border-line bg-card text-ink")}>
                  <button type="button" data-testid="thread-item" aria-current={on ? "true" : undefined} onClick={() => open(t.thread_id)}
                    className={cx("min-h-10 max-w-48 truncate py-1.5 pr-1 pl-3.5", on ? "font-bold" : "font-medium hover:text-brand-700 dark:hover:text-brand-300")}>{t.title || "(제목 없음)"}</button>
                  <button type="button" aria-label="대화 삭제" data-testid="thread-delete" onClick={() => remove(t.thread_id)}
                    className={cx("flex size-10 items-center justify-center text-lg", on ? "hover:bg-brand-800 dark:hover:bg-brand-300" : "text-muted hover:bg-danger-50 hover:text-danger-700 dark:hover:bg-danger-950/60 dark:hover:text-danger-300")}>×</button>
                </span>
              );
            })}
          </div>
        )}
      </div>
      <div ref={logRef} data-testid="chat-log" aria-live="polite" className="flex max-h-[55dvh] min-h-60 flex-col gap-3 overflow-y-auto px-4 sm:px-6">
        {log.length === 0 && (
          <div className="m-auto max-w-xs py-8 text-center text-muted">
            <div aria-hidden="true" className="mb-2 font-hanja text-5xl text-brand-300 dark:text-brand-700">字</div>
            궁금한 한자나 낱말을 물어보세요. 예: &ldquo;학교&rdquo;, &ldquo;學이 뭐예요?&rdquo;
          </div>
        )}
        {log.map((m, i) => (
          <Bubble key={i} role={m.role} testid={`msg-${m.role}`}>
            {m.text || <span className="inline-block animate-pulse">…</span>}
          </Bubble>
        ))}
      </div>
      <div className="space-y-3 px-4 sm:px-6">
        {failed && (
          <Alert as="div" tone="warning" role="alert" data-testid="chat-failed" className="flex flex-wrap items-center justify-between gap-3">
            <span>{failed.kind === "timeout" ? "답이 너무 오래 걸려요." : "답을 만들지 못했어요."} 다시 시도해 볼까요?</span>
            <Button size="sm" data-testid="chat-retry" onClick={() => { const m = failed.message; ask(m); }}>다시 시도</Button>
          </Alert>
        )}
        {notice && <Alert tone="info" data-testid="chat-notice">{notice}</Alert>}
      </div>
      <div className="flex gap-2 rounded-b-(--radius-card) border-t border-line bg-card/95 p-3 backdrop-blur sm:px-6 sm:py-4">
        <input className={cx(inputClass, "min-h-12 flex-1 rounded-full px-5")} aria-label="질문 입력" data-testid="chat-input" value={text} disabled={locked || busy} placeholder="한자나 낱말을 물어봐요" enterKeyHint="send"
          onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send()} />
        <Button className="min-h-12 rounded-full px-5" data-testid="chat-send" disabled={locked || busy} onClick={send}>보내기</Button>
      </div>
    </Card>
  );
}
