export type Manifest = {
  name?: string;
  short_name?: string;
  start_url?: string;
  display?: string;
  icons?: { src: string; sizes: string; type?: string; purpose?: string }[];
};

/** Chrome installability essentials (checked by a unit test and again in E2E). */
export function manifestProblems(m: Manifest): string[] {
  const p: string[] = [];
  if (!m.name && !m.short_name) p.push("name");
  if (!m.start_url) p.push("start_url");
  if (!["standalone", "fullscreen", "minimal-ui"].includes(m.display ?? "")) p.push("display");
  const sizes = (m.icons ?? []).flatMap((i) => i.sizes.split(" "));
  if (!sizes.includes("192x192")) p.push("icon192");
  if (!sizes.includes("512x512")) p.push("icon512");
  return p;
}

export function registerServiceWorker(): void {
  if (typeof navigator !== "undefined" && "serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => undefined);
  }
}
