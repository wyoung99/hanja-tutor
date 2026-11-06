import { describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import vm from "node:vm";
import { bytesToUrlBase64, keyMismatch, subscriptionBody, urlBase64ToUint8Array } from "./push";

// A real VAPID public key shape: 65-byte uncompressed P-256 point, base64url.
const KEY = "BEl62iUYgUivxIkv69yViEuiBIa-Ib9-SkvMeAtA3LFgDzkrxZJjSgSnfckjBJuBkr3qBUYIHBQFLXYp5Nksh8U";

describe("push helpers", () => {
  it("decodes the VAPID applicationServerKey to 65 bytes starting with 0x04", () => {
    const b = urlBase64ToUint8Array(KEY);
    expect(b.length).toBe(65);
    expect(b[0]).toBe(4);
    expect(bytesToUrlBase64(b)).toBe(KEY);
  });
  it("builds the subscription body and rejects incomplete ones", () => {
    const json = { endpoint: "https://fcm.googleapis.com/fcm/send/x", keys: { p256dh: "p", auth: "a" }, expirationTime: null };
    expect(subscriptionBody(json)).toEqual({ endpoint: json.endpoint, keys: { p256dh: "p", auth: "a" } });
    expect(() => subscriptionBody({ endpoint: "x" })).toThrow();
  });
  it("detects a subscription made with another server key", () => {
    expect(keyMismatch(urlBase64ToUint8Array(KEY).buffer as ArrayBuffer, KEY)).toBe(false);
    expect(keyMismatch(new Uint8Array([4, 1, 2]).buffer, KEY)).toBe(true);
    expect(keyMismatch(null, KEY)).toBe(false);
  });
});

/** Load public/sw.js into a fake ServiceWorkerGlobalScope and capture its event handlers. */
function loadWorker(windows: any[] = []) {
  const handlers: Record<string, (e: any) => void> = {};
  const self: any = {
    location: { origin: "https://app.example" },
    addEventListener: (t: string, fn: any) => { handlers[t] = fn; },
    registration: { showNotification: vi.fn(async () => undefined) },
    clients: { matchAll: vi.fn(async () => windows), openWindow: vi.fn(async () => undefined), claim: vi.fn() },
    skipWaiting: vi.fn(),
  };
  const src = readFileSync(resolve(__dirname, "../public/sw.js"), "utf-8");
  vm.runInNewContext(src, { self, caches: {}, fetch: vi.fn(), URL, location: self.location, Promise });
  return { handlers, self };
}

async function fire(h: (e: any) => void, e: any) {
  let p: Promise<unknown> = Promise.resolve();
  h({ ...e, waitUntil: (x: Promise<unknown>) => { p = x; } });
  await p;
}

describe("service worker push", () => {
  it("shows the pushed title/body with tag and click target", async () => {
    const { handlers, self } = loadWorker();
    const msg = { title: "사용 시간 연장 요청", body: "별이이(가) 15분 더 쓰고 싶어해요.", kind: "extension_request",
      id: "i1", tag: "extension_request-3", url: "/?alerts=1" };
    await fire(handlers.push, { data: { json: () => msg, text: () => JSON.stringify(msg) } });
    expect(self.registration.showNotification).toHaveBeenCalledWith("사용 시간 연장 요청", expect.objectContaining({
      body: msg.body, tag: msg.tag, icon: "/icon-192.png", data: { url: "/?alerts=1", kind: "extension_request", id: "i1" } }));
  });
  it("falls back to a default title for a non-JSON payload", async () => {
    const { handlers, self } = loadWorker();
    await fire(handlers.push, { data: { json: () => { throw new Error("x"); }, text: () => "hi" } });
    expect(self.registration.showNotification).toHaveBeenCalledWith("한자 튜터", expect.objectContaining({ body: "hi" }));
  });
  it("focuses an open window and asks it for the alerts tab on click", async () => {
    const win = { url: "https://app.example/", focus: vi.fn(async () => undefined), postMessage: vi.fn() };
    const { handlers, self } = loadWorker([win]);
    const close = vi.fn();
    await fire(handlers.notificationclick, { notification: { close, data: { url: "/?alerts=1", kind: "safety_event" } } });
    expect(close).toHaveBeenCalled();
    expect(win.postMessage).toHaveBeenCalledWith({ type: "open-alerts", kind: "safety_event" });
    expect(win.focus).toHaveBeenCalled();
    expect(self.clients.openWindow).not.toHaveBeenCalled();
  });
  it("opens a window when none is open and never a foreign origin", async () => {
    const { handlers, self } = loadWorker([]);
    await fire(handlers.notificationclick, { notification: { close: vi.fn(), data: { url: "/?alerts=1" } } });
    expect(self.clients.openWindow).toHaveBeenCalledWith("https://app.example/?alerts=1");
    self.clients.openWindow.mockClear();
    await fire(handlers.notificationclick, { notification: { close: vi.fn(), data: { url: "https://evil.example/x" } } });
    expect(self.clients.openWindow).not.toHaveBeenCalled();
  });
});
