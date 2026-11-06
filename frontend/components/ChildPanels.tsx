"use client";
import { ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, childLockMessage, mmss, passLine, pct, scoreLine, streamChat, TYPE_LABEL } from "../lib/api";
import type { Pt } from "../lib/strokes";
import StrokePad from "./StrokePad";

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
      {st?.warning && <p className="banner" role="status" data-testid="time-warning">곧 쉬는 시간이에요! 약 {Math.ceil((st.seconds_left ?? 0) / 60)}분 남았어요.</p>}
      {st?.lock_pending && <p className="banner" data-testid="lock-pending">사용 시간이 끝났어요. 모의고사를 마치면 쉬어요.</p>}
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
    <section className="card" data-testid="app-locked">
      <h2>{reason === "outside_window" ? "지금은 쉬는 시간이에요" : "오늘 사용 시간이 끝났어요"}</h2>
      <label>더 필요한 시간(분)
        <select data-testid="extra-minutes" value={minutes} onChange={(e) => setMinutes(Number(e.target.value))}>
          {[5, 10, 15, 30].map((m) => <option key={m} value={m}>{m}분</option>)}
        </select>
      </label>
      <button data-testid="ask-extension" disabled={req?.status === "pending"} onClick={ask}>부모님께 시간 더 달라고 하기</button>
      <h3>부모님이 옆에 계신가요?</h3>
      <input data-testid="parent-password-extra" type="password" autoComplete="off" placeholder="부모님 계정 비밀번호" value={password} onChange={(e) => setPassword(e.target.value)} />
      <button className="secondary" data-testid="grant-extra" onClick={grant}>부모님 비밀번호로 시간 추가</button>
      <button className="secondary" onClick={onUnlocked}>다시 확인</button>
      {msg && <p data-testid="lock-msg">{msg}</p>}
    </section>
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
  return (
    <section className="card" aria-label="따라쓰기">
      <div className="big">{card.char}</div>
      <p>{reading(card)}</p>
      {mode === "unavailable" && <p data-testid="tracing-unavailable">이 글자는 획순 데이터가 없어서 따라쓰기를 할 수 없어요.</p>}
      {mode === "animation_only" && <p data-testid="animation-only">이 글자는 획순만 볼 수 있어요(채점 제외).</p>}
      {mode === "graded" && <StrokePad onStroke={stroke} disabled={!!result} guide={guide} highlight={answer} wrong={wrong} accepted={accepted} />}
      {mode === "animation_only" && <StrokePad onStroke={() => undefined} disabled guide={guide} />}
      <p data-testid="trace-msg">{msg}</p>
      {result && <p data-testid="trace-result">{result === "pass" ? "통과" : "재시도"}</p>}
      {result && <button data-testid="trace-finish" onClick={() => onDone(result === "pass")}>끝내기</button>}
      <button className="secondary" onClick={() => onDone(false)}>뒤로</button>
    </section>
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
      <section className="card">
        <h2>모의고사 (실전형)</h2>
        <button data-testid="exam-start" onClick={start}>시작하기</button>
        {msg && <p data-testid="exam-msg">{msg}</p>}
      </section>
    );
  return (
    <section className="card" aria-label="모의고사" data-testid="exam-running" data-exam-id={exam.exam_id}>
      <p className="timer" data-testid="exam-timer" aria-live="polite">남은 시간 {mmss(left)}</p>
      <p data-testid="exam-format">제한 시간 {exam.minutes}분 · 합격 {passLine({ ...exam, question_count: exam.questions.length })} · 문항 {exam.questions.length}개</p>
      {exam.questions.map((q: any, i: number) => (
        <div key={q.id} className="card" data-testid="exam-question" data-type={q.type}>
          <p>{i + 1}. {q.prompt}</p>
          {q.figure && <StrokeFigure strokes={q.figure.strokes} highlight={q.figure.highlight} />}
          {q.type === "writing" ? (
            <>
              <StrokePad onStroke={(s) => {
                const next = [...(strokes[q.id] ?? []), s];
                setStrokes((m) => ({ ...m, [q.id]: next }));
                answer(q.id, next);
              }} accepted={strokes[q.id] ?? []} />
              <small>{(strokes[q.id] ?? []).length}획 입력됨</small>
              <button className="secondary" onClick={() => { setStrokes((m) => ({ ...m, [q.id]: [] })); answer(q.id, []); }}>지우기</button>
            </>
          ) : (
            q.options.map((o: string) => (
              <label key={o} className="check"><input type="radio" name={q.id} checked={answers[q.id] === o} onChange={() => answer(q.id, o)} />{o}</label>
            ))
          )}
        </div>
      ))}
      <button data-testid="exam-submit" onClick={submit}>제출하기</button>
    </section>
  );
}

/** 筆順 figure: the char's stroke medians (hanzi-writer data) with the asked stroke in red. */
export function StrokeFigure({ strokes, highlight }: { strokes: [number, number][][]; highlight: number }) {
  const path = (s: [number, number][]) => s.map(([x, y], i) => `${i ? "L" : "M"}${x} ${y}`).join(" ");
  return (
    <svg viewBox="0 0 1000 1000" width={160} height={160} role="img" aria-label="빨간색 획을 보세요" data-testid="stroke-figure"
      style={{ background: "#fff", border: "1px solid #dfe5e1", borderRadius: 8 }}>
      {strokes.map((s, i) => (
        <path key={i} d={path(s)} fill="none" strokeLinecap="round" strokeLinejoin="round"
          stroke={i === highlight ? "#e0533d" : "#9aa59f"} strokeWidth={i === highlight ? 70 : 50} />
      ))}
    </svg>
  );
}

function ExamResult({ result, onClose }: { result: any; onClose: () => void }) {
  return (
    <section className="card" data-testid="exam-result">
      <h2>결과</h2>
      {result.auto_submitted && <p data-testid="exam-auto">시간이 끝나서 자동으로 제출했어요.</p>}
      <p data-testid="exam-score">{scoreLine(result)} (합격선 {passLine(result)})</p>
      <p data-testid="exam-pass">{result.passed ? "합격 🎉" : "불합격 — 다시 도전해요"}</p>
      {result.notice && <p className="explain" data-testid="exam-notice">{result.notice}</p>}
      <h3>유형별 정답률</h3>
      <table data-testid="exam-by-type"><tbody>
        {Object.entries(result.by_type ?? {}).map(([t, v]: [string, any]) => (
          <tr key={t} data-substituted={v.substituted ? "1" : "0"}>
            <td>{v.label ?? TYPE_LABEL[t] ?? t}{v.substituted && <small> (대체: {TYPE_LABEL[v.type] ?? v.type})</small>}</td>
            <td>{v.correct}/{v.total}</td><td>{pct(v.rate)}</td>
          </tr>
        ))}
      </tbody></table>
      <h3>틀린 문항 해설 ({result.wrong.length})</h3>
      <ol data-testid="exam-wrong">
        {result.wrong.map((w: any) => (
          <li key={w.id}>
            <div>{w.prompt}</div>
            <div>내 답: {w.your_answer == null ? "(안 씀)" : String(w.your_answer)} · 정답: <b>{w.answer}</b></div>
            <div className="explain">{w.explanation}</div>
          </li>
        ))}
      </ol>
      <p>틀린 한자는 복습 목록에 들어갔어요.</p>
      <button className="secondary" onClick={onClose}>닫기</button>
    </section>
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

  if (!child.ai_consent) return <section className="card"><h2>한자 친구</h2><p data-testid="chat-disabled">부모님이 허락하면 쓸 수 있어요.</p></section>;
  return (
    <section className="card" aria-label="한자 친구">
      <h2>한자 친구</h2>
      <div className="threads">
        <button className="secondary" data-testid="new-thread" onClick={fresh}>새 대화</button>
        {threads.map((t) => (
          <span key={t.thread_id} className={`thread ${t.thread_id === tid ? "on" : ""}`}>
            <button className="secondary" data-testid="thread-item" onClick={() => open(t.thread_id)}>{t.title || "(제목 없음)"}</button>
            <button className="secondary" aria-label="대화 삭제" data-testid="thread-delete" onClick={() => remove(t.thread_id)}>×</button>
          </span>
        ))}
      </div>
      <div data-testid="chat-log">{log.map((m, i) => <div key={i} className={`msg ${m.role}`} data-testid={`msg-${m.role}`}>{m.text || "…"}</div>)}</div>
      {failed && (
        <div className="banner" role="alert" data-testid="chat-failed">
          {failed.kind === "timeout" ? "답이 너무 오래 걸려요." : "답을 만들지 못했어요."} 다시 시도해 볼까요?
          <button data-testid="chat-retry" onClick={() => { const m = failed.message; ask(m); }}>다시 시도</button>
        </div>
      )}
      {notice && <p data-testid="chat-notice">{notice}</p>}
      <input data-testid="chat-input" value={text} disabled={locked || busy} placeholder="한자나 낱말을 물어봐요" onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send()} />
      <button data-testid="chat-send" disabled={locked || busy} onClick={send}>보내기</button>
    </section>
  );
}
