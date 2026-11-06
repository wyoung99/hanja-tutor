/* Service worker: app-shell cache + offline fallback; never caches /api responses. */
const CACHE = "hanja-shell-v3";
const SHELL = ["/", "/manifest.webmanifest", "/icon-192.png", "/icon-512.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api")) return;
  e.respondWith(
    fetch(e.request)
      .then((res) => {
        if (res.ok && (e.request.mode === "navigate" || SHELL.includes(url.pathname))) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
        }
        return res;
      })
      .catch(() => caches.match(e.request).then((r) => r || caches.match("/")))
  );
});

// Web Push (VAPID) from the backend's NotificationCenter: {title, body, kind, id, tag, url}.
// The payload only carries system text (never a child's chat message).
self.addEventListener("push", (e) => {
  let d = {};
  try {
    d = e.data ? e.data.json() : {};
  } catch (_) {
    d = { body: e.data ? e.data.text() : "" };
  }
  const title = d.title || "한자 튜터";
  e.waitUntil(
    self.registration.showNotification(title, {
      body: d.body || "",
      icon: "/icon-192.png",
      badge: "/icon-192.png",
      tag: d.tag || undefined,
      data: { url: d.url || "/?alerts=1", kind: d.kind || null, id: d.id || null },
    })
  );
});

// Tapping a notification focuses an open app window and asks it to show the alerts tab (a message, not a
// navigation, so the parent's in-memory session survives) or opens a new window on /?alerts=1.
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const target = new URL((e.notification.data && e.notification.data.url) || "/?alerts=1", self.location.origin);
  if (target.origin !== self.location.origin) return; // never open foreign URLs
  e.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((wins) => {
      for (const w of wins) {
        if (new URL(w.url).origin === self.location.origin && "focus" in w) {
          w.postMessage({ type: "open-alerts", kind: e.notification.data && e.notification.data.kind });
          return w.focus();
        }
      }
      return self.clients.openWindow(target.href);
    })
  );
});

// The browser rotated the subscription: the next login re-registers it (no session in the worker).
self.addEventListener("pushsubscriptionchange", () => {});
