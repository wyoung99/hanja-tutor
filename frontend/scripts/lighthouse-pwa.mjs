// AC17: Lighthouse PWA check on the local stack, results written to reports/.
//
//   npm --prefix frontend run lighthouse            (builds if .next is missing)
//
// Starts a local uvicorn (fake LLM, no .env) and `next start`, runs Lighthouse 11 (the last major with the
// PWA category) in Playwright's bundled Chromium, and writes
//   reports/lighthouse-pwa.json  summary (category score + installability/service-worker audits)
//   reports/lighthouse-pwa.html  full Lighthouse report
// Exit code 1 when the installability audits fail.
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import * as chromeLauncher from "chrome-launcher";
import lighthouse from "lighthouse";
import { chromium } from "@playwright/test";

const here = dirname(fileURLToPath(import.meta.url));
const FRONT = resolve(here, "..");
const ROOT = resolve(FRONT, "..");
const OUT = resolve(ROOT, "reports");
const API = 8310;
const WEB = 3310;
// Lighthouse 11 checks the service worker inside "installable-manifest" (it must control start_url).
const KEY_AUDITS = ["installable-manifest", "splash-screen", "themed-omnibox", "maskable-icon",
  "content-width", "viewport", "apple-touch-icon"];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function waitFor(url, ms = 120_000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    try { if ((await fetch(url)).ok) return; } catch { /* not up yet */ }
    await sleep(500);
  }
  throw new Error(`timeout waiting for ${url}`);
}
function run(cmd, args, opts) {
  return spawn(cmd, args, { stdio: "ignore", shell: process.platform === "win32", ...opts });
}
function kill(p) {
  if (!p || p.exitCode !== null) return;
  if (process.platform === "win32") spawn("taskkill", ["/pid", String(p.pid), "/T", "/F"], { stdio: "ignore" });
  else p.kill("SIGTERM");
}

async function main() {
  mkdirSync(OUT, { recursive: true });
  if (!existsSync(resolve(FRONT, ".next", "BUILD_ID"))) {
    await new Promise((ok, bad) => run("npm", ["run", "build"], { cwd: FRONT, stdio: "inherit" }).on("exit", (c) => (c ? bad(new Error("build failed")) : ok())));
  }
  const api = run("python", ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(API)], {
    cwd: resolve(ROOT, "backend"), env: { ...process.env, PYTHONPATH: ".", HANJA_SKIP_DOTENV: "1", LLM_CONFIG: "" } });
  const web = run("npx", ["next", "start", "-p", String(WEB)], { cwd: FRONT, env: { ...process.env, BACKEND_URL: `http://127.0.0.1:${API}` } });
  let chrome;
  try {
    await waitFor(`http://127.0.0.1:${API}/api/health`);
    await waitFor(`http://127.0.0.1:${WEB}/`);
    chrome = await chromeLauncher.launch({ chromePath: chromium.executablePath(), chromeFlags: ["--headless=new", "--no-sandbox"] });
    const url = `http://localhost:${WEB}/`;
    const r = await lighthouse(url, { port: chrome.port, output: ["json", "html"], onlyCategories: ["pwa"], logLevel: "error" });
    const lhr = r.lhr;
    const audits = Object.fromEntries(KEY_AUDITS.filter((k) => lhr.audits[k]).map((k) => [k, {
      score: lhr.audits[k].score, title: lhr.audits[k].title,
      ...(lhr.audits[k].score === 1 || lhr.audits[k].score === null ? {} : { explanation: lhr.audits[k].explanation ?? null }),
    }]));
    const installable = lhr.audits["installable-manifest"]?.score === 1;
    const summary = {
      measured_at: new Date().toISOString(), environment: "local next start + uvicorn", url,
      lighthouse_version: lhr.lighthouseVersion, browser: lhr.environment?.hostUserAgent,
      pwa_score: lhr.categories.pwa?.score ?? null, installable,
      service_worker_note: "verified by installable-manifest (service worker controlling start_url)", audits,
      run_warnings: lhr.runWarnings,
    };
    writeFileSync(resolve(OUT, "lighthouse-pwa.json"), JSON.stringify(summary, null, 2));
    writeFileSync(resolve(OUT, "lighthouse-pwa.html"), r.report[1]);
    console.log(JSON.stringify({ pwa_score: summary.pwa_score, installable }));
    process.exitCode = installable ? 0 : 1;
  } finally {
    if (chrome) await chrome.kill();
    kill(web);
    kill(api);
  }
}

main().catch((e) => { console.error(e); process.exitCode = 1; });
