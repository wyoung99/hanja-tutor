// UI review screenshots (Tailwind redesign) on the local stack, saved to reports/ui-screenshots/.
//
//   npm --prefix frontend run screenshots          (rebuilds .next against its own backend port)
//
// Starts a throw-away backend (fake LLM, no .env, embedded PostgreSQL in backend/.pgdata/shots, reset first) and
// `next start`, then captures every main screen at 1280px (desktop) and 390px (phone), light and dark, and checks
// that no screen scrolls horizontally at 390px. Exit code 1 when a screen overflows.
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const here = dirname(fileURLToPath(import.meta.url));
const FRONT = resolve(here, "..");
const ROOT = resolve(FRONT, "..");
const OUT = resolve(ROOT, "reports", "ui-screenshots");
const API = 8420;
const WEB = 3420;
const BASE = `http://127.0.0.1:${WEB}`;
const PARENT_PW = "parent-pw-1";
const CHILD_PW = "kid1234";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function waitFor(url, ms = 180_000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    try { if ((await fetch(url)).ok) return; } catch { /* not up yet */ }
    await sleep(500);
  }
  throw new Error(`timeout waiting for ${url}`);
}
const run = (cmd, args, opts) => spawn(cmd, args, { stdio: "ignore", shell: process.platform === "win32", ...opts });
function kill(p) {
  if (!p || p.exitCode !== null) return;
  if (process.platform === "win32") spawn("taskkill", ["/pid", String(p.pid), "/T", "/F"], { stdio: "ignore" });
  else p.kill("SIGTERM");
}
const exec = (cmd, args, opts) => new Promise((ok, bad) => run(cmd, args, { stdio: "inherit", ...opts }).on("exit", (c) => (c ? bad(new Error(`${cmd} ${args.join(" ")} failed`)) : ok())));

const COMBOS = [
  { name: "desktop-light", viewport: { width: 1280, height: 860 }, colorScheme: "light" },
  { name: "desktop-dark", viewport: { width: 1280, height: 860 }, colorScheme: "dark" },
  { name: "phone-light", viewport: { width: 390, height: 844 }, colorScheme: "light", isMobile: true, hasTouch: true, deviceScaleFactor: 2 },
  { name: "phone-dark", viewport: { width: 390, height: 844 }, colorScheme: "dark", isMobile: true, hasTouch: true, deviceScaleFactor: 2 },
];

async function main() {
  mkdirSync(OUT, { recursive: true });
  // the /api rewrite target is baked in at build time, so build against this script's backend port
  await exec("npm", ["run", "build"], { cwd: FRONT, env: { ...process.env, BACKEND_URL: `http://127.0.0.1:${API}` } });
  const backendEnv = { ...process.env, PYTHONPATH: ".", HANJA_SKIP_DOTENV: "1", LLM_CONFIG: "", PG_EMBEDDED_DIR: ".pgdata/shots" };
  await exec("python", ["-m", "db.pg", "reset", ".pgdata/shots"], { cwd: resolve(ROOT, "backend"), env: backendEnv, stdio: "ignore" });
  const api = run("python", ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(API)], { cwd: resolve(ROOT, "backend"), env: backendEnv });
  const web = run("npx", ["next", "start", "-p", String(WEB)], { cwd: FRONT, env: { ...process.env, BACKEND_URL: `http://127.0.0.1:${API}` } });
  const browser = await chromium.launch();
  const overflow = [];
  const shots = [];
  try {
    await waitFor(`http://127.0.0.1:${API}/api/health`);
    await waitFor(`${BASE}/`);

    // --- data: two parents (max 3 children each) with two children; one child per combo (own weekly mock exam)
    const stamp = (Date.now() % 1e6).toString(36);
    const setup = await browser.newContext({ baseURL: `http://127.0.0.1:${API}` });
    const req = setup.request;
    const ok = async (r) => { if (!r.ok()) throw new Error(`${r.url()} ${r.status()} ${await r.text()}`); return r.json(); };
    const nicks = ["하늘", "바다", "솔이", "별이"];
    const kids = [];
    for (const p of [0, 1]) {
      const parentId = `mom${stamp}${p}`;
      await ok(await req.post("/api/parents/signup", { data: { login_id: parentId, password: PARENT_PW, email: `${parentId}@example.com`, agree_terms: true, agree_privacy: true, agree_legal_guardian: true } }));
      const login = await ok(await req.post("/api/auth/login", { data: { login_id: parentId, password: PARENT_PW } }));
      const H = { Authorization: `Bearer ${login.session}` };
      for (const i of [2 * p, 2 * p + 1]) {
        const c = await ok(await req.post("/api/children", { headers: H, data: { login_id: `kid${stamp}${i}`, password: CHILD_PW, nickname: nicks[i], ai_consent: true } }));
        await ok(await req.put(`/api/children/${c.id}/target`, { headers: H, data: { body: "korea_eomunhoe", grade: "8" } }));
        kids.push({ nick: nicks[i], loginId: `kid${stamp}${i}`, parentId });
      }
    }
    await setup.close();

    for (const [ci, combo] of COMBOS.entries()) {
      const { name, ...opts } = combo;
      const ctx = await browser.newContext({ baseURL: BASE, serviceWorkers: "block", locale: "ko-KR", ...opts });
      const page = await ctx.newPage();
      const phone = name.startsWith("phone");
      async function shot(screen, { full = true } = {}) {
        await page.waitForTimeout(350); // let transitions settle
        const file = `${screen}-${name}.png`;
        await page.screenshot({ path: resolve(OUT, file), fullPage: full });
        shots.push(file);
        if (phone) {
          const o = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
          if (o.sw > o.cw) overflow.push(`${file}: scrollWidth ${o.sw} > ${o.cw}`);
        }
      }
      const logIn = async (id, pw) => {
        await page.goto("/");
        await page.getByTestId("login-id").fill(id);
        await page.getByTestId("login-password").fill(pw);
        await page.getByTestId("login").click();
      };

      // login + signup
      await page.goto("/");
      await page.getByTestId("login-form").waitFor();
      await shot("01-login");
      await page.goto("/signup");
      await page.getByTestId("signup-form").waitFor();
      await shot("02-signup");

      // child: today / tracing / exam / chat
      const kid = kids[ci];
      await logIn(kid.loginId, CHILD_PW);
      await page.getByTestId("child-menu").waitFor();
      await page.getByTestId("today-card").first().waitFor();
      await shot("05-child-today");

      await page.getByTestId("tab-tracing").click();
      await page.getByTestId("tracing-card").first().waitFor();
      await page.locator('[data-testid^="trace-"]').first().click();
      const pad = page.getByTestId("stroke-pad");
      await pad.waitFor();
      const box = await pad.boundingBox();
      const at = (x, y) => [box.x + (x / 1000) * box.width, box.y + (y / 1000) * box.height];
      await page.mouse.move(...at(150, 850));
      await page.mouse.down();
      await page.mouse.move(...at(850, 150), { steps: 8 });
      await page.mouse.up();
      await page.getByTestId("trace-msg").filter({ hasNotText: "획순대로" }).waitFor();
      await shot("06-child-tracing");

      await page.getByTestId("tab-exam").click();
      await page.getByTestId("exam-start").click();
      await page.getByTestId("exam-running").waitFor();
      const questions = page.getByTestId("exam-question");
      for (let i = 0; i < 3; i++) await questions.nth(i).locator('input[type="radio"]').first().check();
      await page.evaluate(() => window.scrollTo(0, 0));
      await shot("07-child-exam-question", { full: false });
      await page.getByTestId("exam-submit").click();
      await page.getByTestId("exam-result").waitFor();
      await page.evaluate(() => window.scrollTo(0, 0));
      await shot("08-child-exam-result", { full: false }); // the full wrong-answer list is very long

      await page.getByTestId("tab-chat").click();
      for (const q of ["學이 뭐예요?", "학교"]) {
        const n = await page.getByTestId("msg-bot").count();
        await page.getByTestId("chat-input").fill(q);
        await page.getByTestId("chat-send").click();
        await page.getByTestId("msg-bot").nth(n).filter({ hasText: /\S{2,}/ }).waitFor();
        await page.getByTestId("chat-input").and(page.locator(":enabled")).waitFor();
      }
      await shot("09-child-chat");
      await page.getByTestId("logout").click();

      // parent: kids (with the plan editor open) and report + conversations
      await logIn(kid.parentId, PARENT_PW);
      await page.getByTestId("parent-menu").waitFor();
      await page.getByTestId("child-card").first().waitFor();
      await page.getByTestId(`plan-${kid.nick}`).click();
      await page.getByTestId("plan-summary").waitFor();
      await shot("03-parent-kids");
      await page.getByTestId("tab-report").click();
      await page.getByTestId("report-child").selectOption({ label: kid.nick });
      await page.getByTestId("basic-report").waitFor();
      await page.getByTestId("pc-thread").first().click();
      await page.getByTestId("pc-messages").waitFor();
      await shot("04-parent-report");
      await ctx.close();
      console.log(`captured ${name}`);
    }
  } finally {
    await browser.close().catch(() => undefined);
    kill(web);
    kill(api);
  }
  writeFileSync(resolve(OUT, "index.json"), JSON.stringify({ shots: shots.sort(), overflow_390px: overflow }, null, 2) + "\n");
  console.log(`${shots.length} screenshots in ${OUT}`);
  if (overflow.length) { console.error("horizontal overflow at 390px:\n" + overflow.join("\n")); process.exitCode = 1; }
  else console.log("no horizontal scroll at 390px");
}

main().catch((e) => { console.error(e); process.exitCode = 1; });
