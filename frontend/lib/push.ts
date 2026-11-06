import { api } from "./api";

/** Web Push (VAPID) for parents. The server's public key comes from GET /api/push/config. */
export type PushState = "on" | "off" | "unsupported" | "denied" | "not_configured" | "error";

/** base64url (VAPID applicationServerKey) -> bytes for PushManager.subscribe. */
export function urlBase64ToUint8Array(b64: string): Uint8Array {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const s = (b64 + pad).replace(/-/g, "+").replace(/_/g, "/");
  const raw = typeof atob === "function" ? atob(s) : Buffer.from(s, "base64").toString("binary");
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

export function bytesToUrlBase64(b: ArrayBuffer | Uint8Array | null): string {
  if (!b) return "";
  const u = b instanceof Uint8Array ? b : new Uint8Array(b);
  let s = "";
  for (const x of u) s += String.fromCharCode(x);
  const b64 = typeof btoa === "function" ? btoa(s) : Buffer.from(s, "binary").toString("base64");
  return b64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** Body for POST /api/parents/me/push-subscriptions from PushSubscription.toJSON(). */
export function subscriptionBody(json: { endpoint?: string; keys?: Record<string, string> }) {
  if (!json.endpoint || !json.keys?.p256dh || !json.keys?.auth) throw new Error("incomplete subscription");
  return { endpoint: json.endpoint, keys: { p256dh: json.keys.p256dh, auth: json.keys.auth } };
}

/** True when an existing browser subscription was made with another server key (must be renewed). */
export function keyMismatch(existingKey: ArrayBuffer | null | undefined, publicKey: string): boolean {
  return !!existingKey && bytesToUrlBase64(existingKey) !== publicKey;
}

export function pushSupported(): boolean {
  return typeof window !== "undefined" && "serviceWorker" in navigator && "PushManager" in window && typeof Notification !== "undefined";
}

export async function pushState(): Promise<PushState> {
  if (!pushSupported()) return "unsupported";
  const cfg = await api<{ enabled: boolean; public_key: string | null }>("/push/config").catch(() => null);
  if (!cfg?.enabled) return "not_configured";
  if (Notification.permission === "denied") return "denied";
  const reg = await navigator.serviceWorker.getRegistration();
  const sub = await reg?.pushManager.getSubscription();
  return sub ? "on" : "off";
}

/** Ask permission, subscribe this browser with the server's VAPID key and register it for the parent. */
export async function enablePush(): Promise<PushState> {
  if (!pushSupported()) return "unsupported";
  const cfg = await api<{ enabled: boolean; public_key: string | null }>("/push/config");
  if (!cfg.enabled || !cfg.public_key) return "not_configured";
  const perm = await Notification.requestPermission();
  if (perm !== "granted") return "denied";
  const reg = await navigator.serviceWorker.ready;
  let sub = await reg.pushManager.getSubscription();
  if (sub && keyMismatch(sub.options?.applicationServerKey, cfg.public_key)) {
    await sub.unsubscribe();
    sub = null;
  }
  sub = sub ?? (await reg.pushManager.subscribe({
    userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(cfg.public_key) as BufferSource }));
  await api("/parents/me/push-subscriptions", { auth: true, body: subscriptionBody(sub.toJSON()) });
  return "on";
}

export async function disablePush(): Promise<PushState> {
  if (!pushSupported()) return "unsupported";
  const reg = await navigator.serviceWorker.getRegistration();
  const sub = await reg?.pushManager.getSubscription();
  if (sub) {
    await api("/parents/me/push-subscriptions", { auth: true, method: "DELETE", body: { endpoint: sub.endpoint } }).catch(() => undefined);
    await sub.unsubscribe();
  }
  return "off";
}

export const PUSH_STATE_TEXT: Record<PushState, string> = {
  on: "이 기기에서 푸시 알림을 받고 있어요.",
  off: "푸시 알림이 꺼져 있어요. 켜지 않으면 알림은 앱 안 알림함과 이메일로 받아요.",
  unsupported: "이 브라우저는 푸시 알림을 지원하지 않아요. 알림함과 이메일로 알려 드려요.",
  denied: "브라우저에서 알림이 차단돼 있어요. 브라우저 설정에서 허용해 주세요.",
  not_configured: "서버에 푸시 키가 설정되지 않았어요. 알림함과 이메일로 알려 드려요.",
  error: "푸시 알림을 켜지 못했어요. 잠시 후 다시 시도해 주세요.",
};
