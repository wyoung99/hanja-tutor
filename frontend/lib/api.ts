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
