"use client";
import { useEffect, useState } from "react";
import { api, ApiError, childLockMessage, session, won } from "../lib/api";
import StrokePad from "../components/StrokePad";
import { gradeLabel, type Pt } from "../lib/strokes";

type Child = { id: string; nickname: string; ai_consent: boolean; target: string[] };
type ExamInfo = { status: "available" | "format_pending" | "unsupported"; message: string | null };
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
function Parent({ onEnter }: { onEnter: (c: Child) => void }) {
  const [kids, setKids] = useState<Child[]>([]);
  const [catalog, setCatalog] = useState<Catalog>([]);
  const [nick, setNick] = useState("");
  const [pin, setPin] = useState("");
  const [ai, setAi] = useState(false);
  const [body, setBody] = useState("");
  const [grade, setGrade] = useState("");
  const [tab, setTab] = useState<"kids" | "report" | "billing">("kids");
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
  const pendingNote = entry?.exam[grade]?.status === "format_pending";
  return (
    <>
      <nav>
        <button className="secondary" onClick={() => setTab("kids")}>자녀</button>
        <button className="secondary" data-testid="tab-report" onClick={() => setTab("report")}>리포트</button>
        <button className="secondary" data-testid="tab-billing" onClick={() => setTab("billing")}>구독</button>
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
            {pendingNote && <p data-testid="grade-note">이 급수는 공식 모의고사 형식을 확인하는 중이라 모의고사는 아직 볼 수 없어요. 오늘의 한자·복습·따라쓰기는 모두 할 수 있어요.</p>}
            <label className="check"><input type="checkbox" data-testid="ai-consent" checked={ai} onChange={(e) => setAi(e.target.checked)} />대화·학습 기록의 AI 처리에 동의합니다 (미동의 시 챗봇만 꺼져요)</label>
            <button data-testid="add-child" onClick={addChild}>프로필 만들기</button>
            {err && <p className="err" role="alert">{err}</p>}
          </section>
          {kids.map((k) => (
            <section className="card" key={k.id} data-testid="child-card">
              <strong>{k.nickname}</strong> · {catalog.find((c) => c.body === k.target[0])?.name ?? k.target[0]} {k.target[1] ? gradeLabel(k.target[1]) : ""}
              <div><button data-testid={`enter-${k.nickname}`} onClick={() => onEnter(k)}>공부 시작</button></div>
            </section>
          ))}
        </>
      )}
      {tab === "report" && <Reports kids={kids} />}
      {tab === "billing" && <Billing />}
    </>
  );
}

// ---------------------------------------------------------------- reports (parent only)
function Reports({ kids }: { kids: Child[] }) {
  const [basic, setBasic] = useState<any>(null);
  const [detail, setDetail] = useState<any>(null);
  const [msg, setMsg] = useState("");
  const k = kids[0];
  useEffect(() => {
    if (k) api(`/children/${k.id}/report/basic`, { auth: true }).then(setBasic).catch(() => undefined);
  }, [k?.id]);
  if (!k) return <p>자녀를 먼저 만들어 주세요.</p>;
  async function loadDetail() {
    setMsg("");
    try {
      setDetail(await api(`/children/${k.id}/report/detailed`, { auth: true }));
    } catch (e) {
      setMsg(e instanceof ApiError && e.status === 402 ? "상세 리포트는 구독 전용이에요." : "오류가 발생했어요.");
    }
  }
  return (
    <section className="card" aria-label="리포트">
      <h2>{k.nickname} 기본 리포트 (무료)</h2>
      {basic && (
        <ul data-testid="basic-report">
          <li>학습 시간: {basic.study_minutes_total}분</li>
          <li>오늘의 한자 완료율: {basic.todays_hanja.completion_rate == null ? "-" : Math.round(basic.todays_hanja.completion_rate * 100) + "%"}</li>
          <li>복습 정답률: {basic.review.accuracy == null ? "-" : Math.round(basic.review.accuracy * 100) + "%"}</li>
        </ul>
      )}
      <button data-testid="detail-report" onClick={loadDetail}>상세 리포트 보기</button>
      {msg && <p role="alert" data-testid="detail-msg">{msg}</p>}
      {detail && <pre data-testid="detail-body">{JSON.stringify(detail, null, 1)}</pre>}
    </section>
  );
}

// ---------------------------------------------------------------- billing (parent only)
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
type Card = { char: string; hun: string | null; eum: string | null; reviewed: boolean; strokes: number; tracing: "graded" | "animation_only" | "unavailable" };

/** 훈·음 of the child's selected exam body (empty source rows await human review). */
function reading(c: Card) {
  return c.hun && c.eum ? `${c.hun} ${c.eum}` : "훈·음 검수 중";
}

function ChildApp({ child, onExit }: { child: Child; onExit: () => void }) {
  const [unlocked, setUnlocked] = useState(false);
  const [pin, setPin] = useState("");
  const [err, setErr] = useState("");
  const [tab, setTab] = useState<"today" | "exam" | "chat">("today");
  async function unlock() {
    try { await api(`/children/${child.id}/verify-pin`, { body: { pin } }); setUnlocked(true); setErr(""); }
    catch (e) { setErr(e instanceof ApiError && e.status === 423 ? "잠겼어요. 부모님께 알려 주세요." : "PIN이 달라요."); }
  }
  if (!unlocked)
    return (
      <section className="card">
        <h2>{child.nickname}, PIN을 입력해요</h2>
        <input data-testid="child-pin" type="password" inputMode="numeric" value={pin} onChange={(e) => setPin(e.target.value)} />
        <button data-testid="child-unlock" onClick={unlock}>들어가기</button>
        <button className="secondary" onClick={onExit}>뒤로</button>
        {err && <p className="err" role="alert">{err}</p>}
      </section>
    );
  return (
    <>
      <nav>
        <button className="secondary" data-testid="tab-today" onClick={() => setTab("today")}>오늘의 한자</button>
        <button className="secondary" data-testid="tab-exam" onClick={() => setTab("exam")}>모의고사</button>
        <button className="secondary" data-testid="tab-chat" onClick={() => setTab("chat")}>한자 친구</button>
        <button className="secondary" onClick={onExit}>나가기</button>
      </nav>
      {tab === "today" && <Today child={child} />}
      {tab === "exam" && <Exam child={child} />}
      {tab === "chat" && <Chat child={child} />}
    </>
  );
}

function Today({ child }: { child: Child }) {
  const [data, setData] = useState<{ new: Card[]; reviews: Card[] } | null>(null);
  const [tracing, setTracing] = useState<Card | null>(null);
  const [done, setDone] = useState<string[]>([]);
  const load = () => api(`/children/${child.id}/today`).then(setData);
  useEffect(() => { load(); }, []);
  if (tracing)
    return <Tracing child={child} card={tracing} onDone={async (ok) => {
      if (ok) { await api(`/children/${child.id}/today/${tracing.char}/complete`, { body: {} }).catch(() => undefined); setDone((d) => [...d, tracing.char]); }
      setTracing(null);
    }} />;
  if (!data) return <p>불러오는 중…</p>;
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
        <div key={c.char}>{c.char} {reading(c)}
          <button onClick={async () => { await api(`/children/${child.id}/reviews/${c.char}`, { body: { correct: true } }); load(); }}>알아요</button>
          <button className="secondary" onClick={async () => { await api(`/children/${child.id}/reviews/${c.char}`, { body: { correct: false } }); load(); }}>몰라요</button>
        </div>
      ))}
    </section>
  );
}

function Tracing({ child, card, onDone }: { child: Child; card: Card; onDone: (ok: boolean) => void }) {
  const [mode, setMode] = useState<string>("");
  const [guide, setGuide] = useState<Pt[][]>([]);
  const [highlight, setHighlight] = useState<Pt[] | null>(null);
  const [msg, setMsg] = useState("획순대로 한 획씩 써 보세요.");
  const [result, setResult] = useState("");
  useEffect(() => {
    api(`/children/${child.id}/tracing/${card.char}/start`, { body: {} }).then((r) => { setMode(r.mode); setGuide(r.strokes ?? []); });
  }, []);
  async function stroke(s: Pt[]) {
    const r = await api(`/children/${child.id}/tracing/${card.char}/stroke`, { body: { stroke: s } });
    setHighlight(r.reveal_answer ? r.answer_stroke : null);
    if (r.done) { setResult(r.result); setMsg(r.result === "pass" ? "통과! 잘 썼어요 🎉" : "정답 획을 봐서 다시 도전이에요."); }
    else if (r.ok) setMsg("좋아요! 다음 획이에요.");
    else setMsg(r.reveal_answer ? "빨간 획이 정답이에요. 따라 써 볼까요?" : "다시 써 볼까요?");
  }
  return (
    <section className="card" aria-label="따라쓰기">
      <div className="big">{card.char}</div>
      <p>{reading(card)}</p>
      {mode === "unavailable" && <p data-testid="tracing-unavailable">이 글자는 획순 데이터가 없어서 따라쓰기를 할 수 없어요.</p>}
      {mode === "animation_only" && <p data-testid="animation-only">이 글자는 획순만 볼 수 있어요(채점 제외).</p>}
      {mode === "graded" && <StrokePad onStroke={stroke} disabled={!!result} guide={guide} highlight={highlight} />}
      {mode === "animation_only" && <StrokePad onStroke={() => undefined} disabled guide={guide} />}
      <p data-testid="trace-msg">{msg}</p>
      {result && <button data-testid="trace-finish" onClick={() => onDone(result === "pass")}>끝내기</button>}
      <button className="secondary" onClick={() => onDone(false)}>뒤로</button>
    </section>
  );
}

function Exam({ child }: { child: Child }) {
  const [exam, setExam] = useState<any>(null);
  const [answers, setAnswers] = useState<Record<string, unknown>>({});
  const [result, setResult] = useState<any>(null);
  const [msg, setMsg] = useState("");
  const [strokes, setStrokes] = useState<Record<string, Pt[][]>>({});
  async function start() {
    setMsg("");
    try { setExam(await api(`/children/${child.id}/exams`, { body: {} })); }
    catch (e) {
      if (e instanceof ApiError && e.status === 429) setMsg(childLockMessage("mockexam"));
      else if (e instanceof ApiError && e.detail?.code === "exam_format_pending") setMsg("이 급수의 모의고사는 형식 준비 중이에요. 오늘의 한자와 따라쓰기로 공부해요!");
      else setMsg("시작할 수 없어요.");
    }
  }
  async function answer(qid: string, value: unknown) {
    setAnswers((a) => ({ ...a, [qid]: value }));
    await api(`/exams/${exam.exam_id}/answer`, { body: { question_id: qid, value } });
  }
  async function submit() { setResult(await api(`/exams/${exam.exam_id}/submit`, { body: {} })); }
  if (result)
    return (
      <section className="card" data-testid="exam-result">
        <h2>결과</h2>
        <p data-testid="exam-score">{result.score} / {result.total}점</p>
        <p data-testid="exam-pass">{result.passed ? "합격 🎉" : "불합격 — 다시 도전해요"}</p>
      </section>
    );
  if (!exam)
    return (
      <section className="card">
        <h2>모의고사 (실전형)</h2>
        <button data-testid="exam-start" onClick={start}>시작하기</button>
        {msg && <p data-testid="exam-msg">{msg}</p>}
      </section>
    );
  return (
    <section className="card" aria-label="모의고사">
      <p>제한 시간 {exam.minutes}분 · 합격 {exam.pass_score}/{exam.total_score}점 · 문항 {exam.questions.length}개</p>
      {exam.questions.map((q: any, i: number) => (
        <div key={q.id} className="card" data-testid="exam-question">
          <p>{i + 1}. {q.prompt}</p>
          {q.type === "writing" ? (
            <>
              <StrokePad onStroke={(s) => {
                const next = [...(strokes[q.id] ?? []), s];
                setStrokes((m) => ({ ...m, [q.id]: next }));
                answer(q.id, next);
              }} />
              <small>{(strokes[q.id] ?? []).length}획 입력됨</small>
            </>
          ) : q.options.length ? (
            q.options.map((o: string) => (
              <label key={o} className="check"><input type="radio" name={q.id} checked={answers[q.id] === o} onChange={() => answer(q.id, o)} />{o}</label>
            ))
          ) : (
            <input value={(answers[q.id] as string) ?? ""} onChange={(e) => answer(q.id, e.target.value)} />
          )}
        </div>
      ))}
      <button data-testid="exam-submit" onClick={submit}>제출하기</button>
    </section>
  );
}

function Chat({ child }: { child: Child }) {
  const [log, setLog] = useState<{ role: "user" | "bot"; text: string }[]>([]);
  const [text, setText] = useState("");
  const [locked, setLocked] = useState(false);
  async function send() {
    const m = text.trim();
    if (!m) return;
    setText("");
    setLog((l) => [...l, { role: "user", text: m }]);
    try {
      const r = await api(`/children/${child.id}/chat`, { body: { message: m, thread_id: "t-" + child.id } });
      if (r.status === "locked") { setLocked(true); setLog((l) => [...l, { role: "bot", text: childLockMessage("chatbot") }]); }
      else setLog((l) => [...l, { role: "bot", text: r.reply }]);
    } catch (e) {
      setLog((l) => [...l, { role: "bot", text: e instanceof ApiError && e.status === 403 ? "부모님이 허락하면 쓸 수 있어요." : "지금은 답하기 어려워요. 잠시 뒤에 다시 물어봐 주세요." }]);
    }
  }
  return (
    <section className="card" aria-label="한자 친구">
      <h2>한자 친구</h2>
      <div data-testid="chat-log">{log.map((m, i) => <div key={i} className={`msg ${m.role}`} data-testid={`msg-${m.role}`}>{m.text}</div>)}</div>
      <input data-testid="chat-input" value={text} disabled={locked} placeholder="한자나 낱말을 물어봐요" onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send()} />
      <button data-testid="chat-send" disabled={locked} onClick={send}>보내기</button>
    </section>
  );
}
