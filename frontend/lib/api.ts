export class ApiError extends Error {
  constructor(public status: number, public detail: any) {
    super(typeof detail === "object" && detail?.code ? detail.code : String(status));
  }
}

// ---------------------------------------------------------------- auth (id + password, one login for both roles)
export type Role = "parent" | "child";
/** What the app keeps after login. The server decides the role from the id; the token is a bearer session. */
export type Auth = { token: string; role: Role; accountId: string; parentId: string; nickname?: string | null };
export type Session = { token: string | null; auth: Auth | null };
export const session: Session = { token: null, auth: null };
const AUTH_KEY = "hanja.auth";
export const LOGOUT_EVENT = "hanja:logout";

function storage(): Storage | null {
  try { return typeof localStorage !== "undefined" ? localStorage : null; } catch { return null; }
}

export function setAuth(a: Auth | null) {
  session.auth = a;
  session.token = a?.token ?? null;
  try {
    if (a) storage()?.setItem(AUTH_KEY, JSON.stringify(a));
    else storage()?.removeItem(AUTH_KEY);
  } catch { /* private mode: in-memory session only */ }
}

export function loadAuth(): Auth | null {
  try {
    const a = JSON.parse(storage()?.getItem(AUTH_KEY) ?? "null");
    if (a && typeof a.token === "string" && (a.role === "parent" || a.role === "child")) {
      session.auth = a; session.token = a.token;
      return a;
    }
  } catch { /* ignore a corrupt entry */ }
  return null;
}

/** POST /auth/login response -> Auth. */
export function authFromLogin(r: { session: string; role: Role; account_id: string; parent_id: string; nickname?: string | null }): Auth {
  return { token: r.session, role: r.role, accountId: r.account_id, parentId: r.parent_id, nickname: r.nickname ?? null };
}

/** Menus per role: a child gets learning screens only, a parent management screens only. */
export const CHILD_MENUS = [
  { key: "today", label: "오늘의 한자" }, { key: "review", label: "복습" }, { key: "tracing", label: "따라쓰기" },
  { key: "exam", label: "모의고사" }, { key: "chat", label: "챗봇" },
] as const;
export const PARENT_MENUS = [
  { key: "kids", label: "자녀 관리" }, { key: "report", label: "리포트·대화" }, { key: "alerts", label: "알림" },
  { key: "billing", label: "구독" }, { key: "account", label: "계정" },
] as const;
export function menusFor(role: Role) {
  return role === "child" ? CHILD_MENUS : PARENT_MENUS;
}

/** Same rules as the server (accounts/service.py). Returns a Korean message or "" when valid. */
export const LOGIN_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{3,19}$/;
export function loginIdError(id: string): string {
  return LOGIN_ID_RE.test(id.trim()) ? "" : "아이디는 영문·숫자로 시작하는 4~20자(영문·숫자·. _ -)예요.";
}
export function passwordError(pw: string, role: Role): string {
  if (pw.length > 128) return "비밀번호는 128자 이하로 정해 주세요.";
  if (role === "child") return pw.length >= 4 ? "" : "자녀 비밀번호는 4자 이상이에요.";
  return pw.length >= 8 && /[A-Za-z]/.test(pw) && /\d/.test(pw) ? "" : "비밀번호는 영문과 숫자를 섞어 8자 이상이에요.";
}
export type SignupForm = { loginId: string; password: string; confirm: string; email: string; terms: boolean; privacy: boolean; guardian: boolean };
export function signupError(f: SignupForm): string {
  return loginIdError(f.loginId) || passwordError(f.password, "parent")
    || (f.password !== f.confirm ? "비밀번호 확인이 달라요." : "")
    || (/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(f.email.trim()) ? "" : "이메일을 확인해 주세요.")
    || (f.terms && f.privacy && f.guardian ? "" : "필수 동의 항목에 모두 동의해 주세요.");
}

/** Korean text for server error codes shown on the auth screens. */
export function authMessage(e: unknown): string {
  if (!(e instanceof ApiError)) return "연결에 실패했어요. 잠시 후 다시 해 주세요.";
  const d = e.detail ?? {};
  switch (d.code) {
    case "invalid_credentials":
      return `아이디 또는 비밀번호가 달라요.${d.remaining != null ? ` (${d.remaining}번 더 틀리면 잠겨요)` : ""}`;
    case "profile_locked": return "비밀번호를 5번 틀려서 잠겼어요. 부모님께 새 비밀번호를 정해 달라고 하세요.";
    case "account_dormant": return "휴면 계정이에요. 부모님이 먼저 로그인하면 다시 쓸 수 있어요.";
    case "login_id_taken": return "이미 쓰고 있는 아이디예요.";
    case "consent_required": return "필수 동의 항목에 모두 동의해 주세요.";
    default: return d.message ?? d.code ?? "오류가 발생했어요.";
  }
}

function authHeaders(): Record<string, string> {
  return session.token ? { Authorization: `Bearer ${session.token}` } : {};
}

function sessionExpired(status: number, path: string) {
  // a stale/revoked session (logout elsewhere, password reset, dormancy): back to the login screen
  if (status === 401 && session.token && !path.startsWith("/auth/login")) {
    setAuth(null);
    if (typeof window !== "undefined") window.dispatchEvent(new Event(LOGOUT_EVENT));
  }
}

/** Every call carries the current session (parent or child); the server enforces what each role may do. */
export async function api<T = any>(path: string, init: { method?: string; body?: unknown; admin?: string } = {}): Promise<T> {
  const headers: Record<string, string> = { ...authHeaders() };
  if (init.body !== undefined) headers["Content-Type"] = "application/json";
  if (init.admin) headers["X-Admin-Token"] = init.admin;
  const res = await fetch(`/api${path}`, {
    method: init.method ?? (init.body !== undefined ? "POST" : "GET"),
    headers,
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    sessionExpired(res.status, path);
    throw new ApiError(res.status, data?.detail ?? data);
  }
  return data as T;
}

/** Child-facing copy: never mentions subscriptions (parent screens only). */
export function childLockMessage(feature: "chatbot" | "mockexam"): string {
  return feature === "chatbot" ? "오늘은 여기까지! 내일 또 만나요 👋" : "이번 주 모의고사는 다 봤어요. 다음 주에 또 만나요!";
}

export function won(n: number): string {
  return `${n.toLocaleString("ko-KR")}원`;
}

// ---------------------------------------------------------------- SSE chat stream (AC9)
export type SseEvent = { type: string; [k: string]: any };

/** Split an SSE buffer into complete `data:` events; `rest` is the unfinished tail. */
export function parseSse(buffer: string): { events: SseEvent[]; rest: string } {
  const frames = buffer.replace(/\r\n/g, "\n").split("\n\n");
  const rest = frames.pop() ?? "";
  const events: SseEvent[] = [];
  for (const f of frames) {
    const data = f.split("\n").filter((l) => l.startsWith("data: ")).map((l) => l.slice(6)).join("\n");
    if (!data) continue;
    try { events.push(JSON.parse(data)); } catch { /* ignore malformed frame */ }
  }
  return { events, rest };
}

export const CHAT_TIMEOUT_MS = 15_000;
export type StreamOutcome = "done" | "error" | "timeout";

/**
 * POST a chat message and feed SSE events to `onEvent`. Resolves "timeout" when the whole answer did not
 * finish within `timeoutMs` (15 s), "error" on a server error event or network failure.
 * HTTP errors before streaming (403 consent, 423 screen-time lock, 404 thread) throw ApiError.
 */
export async function streamChat(path: string, body: unknown, onEvent: (e: SseEvent) => void,
  timeoutMs = CHAT_TIMEOUT_MS): Promise<StreamOutcome> {
  const ctl = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; ctl.abort(); }, timeoutMs);
  try {
    const res = await fetch(`/api${path}`, {
      method: "POST", headers: { "Content-Type": "application/json", Accept: "text/event-stream", ...authHeaders() },
      body: JSON.stringify(body), signal: ctl.signal,
    });
    if (!res.ok) {
      const data = await res.json().catch(() => null);
      sessionExpired(res.status, path);
      throw new ApiError(res.status, data?.detail ?? data);
    }
    const reader = res.body!.getReader();
    const dec = new TextDecoder();
    let buf = "";
    let outcome: StreamOutcome = "error";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      const parsed = parseSse(buf + dec.decode(value, { stream: true }));
      buf = parsed.rest;
      for (const e of parsed.events) {
        onEvent(e);
        if (e.type === "done") outcome = "done";
        if (e.type === "error") outcome = "error";
      }
    }
    return outcome;
  } catch (e) {
    if (e instanceof ApiError) throw e;
    return timedOut ? "timeout" : "error";
  } finally {
    clearTimeout(timer);
  }
}

/** "MM:SS" for the exam timer. */
export function mmss(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

/** Fallback labels per app question type (results carry the official type name in by_type[k].label). */
export const TYPE_LABEL: Record<string, string> = {
  eum: "음(소리) 고르기", hun_eum: "훈음 고르기", hun_to_hanja: "한자 고르기", writing: "한자 쓰기", busu: "부수", pilsun: "필순",
};

export type ExamScoring = { score?: number; total?: number; total_score?: number; pass_score: number; points_per_question?: number | null;
  pass_questions?: number | null; correct_count?: number; question_count?: number; questions?: number };

/** Pass line: points, plus the official question count when a question is worth more than 1 point. */
export function passLine(f: ExamScoring): string {
  const total = f.total ?? f.total_score;
  const n = f.question_count ?? f.questions;
  const pts = `${f.pass_score}/${total}점`;
  return f.points_per_question && f.points_per_question > 1 && f.pass_questions
    ? `${pts} (문항 기준 ${f.pass_questions}/${n}문항, 1문항 ${f.points_per_question}점)`
    : pts;
}

/** Result score line: "72 / 100점 · 18/25문항 맞힘" (points-based bodies) or "40 / 50점". */
export function scoreLine(r: ExamScoring): string {
  const base = `${r.score} / ${r.total}점`;
  return r.points_per_question && r.points_per_question > 1 ? `${base} · ${r.correct_count}/${r.question_count}문항 맞힘` : base;
}

export function pct(x: number | null | undefined): string {
  return x == null ? "-" : `${Math.round(x * 100)}%`;
}
