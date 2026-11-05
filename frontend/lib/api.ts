export class ApiError extends Error {
  constructor(public status: number, public detail: any) {
    super(typeof detail === "object" && detail?.code ? detail.code : String(status));
  }
}

export type Session = { token: string | null };
export const session: Session = { token: null };

export async function api<T = any>(path: string, init: { method?: string; body?: unknown; auth?: boolean; admin?: string } = {}): Promise<T> {
  const headers: Record<string, string> = {};
  if (init.body !== undefined) headers["Content-Type"] = "application/json";
  if (init.auth && session.token) headers["Authorization"] = `Bearer ${session.token}`;
  if (init.admin) headers["X-Admin-Token"] = init.admin;
  const res = await fetch(`/api${path}`, {
    method: init.method ?? (init.body !== undefined ? "POST" : "GET"),
    headers,
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, data?.detail ?? data);
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
      method: "POST", headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(body), signal: ctl.signal,
    });
    if (!res.ok) {
      const data = await res.json().catch(() => null);
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

/** Accuracy per question type for the exam result screen. */
export const TYPE_LABEL: Record<string, string> = { eum: "음 쓰기", hun_eum: "훈음 쓰기", hun_to_hanja: "한자 고르기", writing: "한자 쓰기" };

export function pct(x: number | null | undefined): string {
  return x == null ? "-" : `${Math.round(x * 100)}%`;
}
