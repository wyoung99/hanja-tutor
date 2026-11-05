import { defineConfig } from "@playwright/test";
import { generateKeyPairSync } from "node:crypto";

/** A throw-away VAPID key pair for the local E2E backend (real Web Push config, no secrets in the repo). */
function vapidKeys() {
  const { privateKey, publicKey } = generateKeyPairSync("ec", { namedCurve: "prime256v1" });
  const pub = publicKey.export({ format: "jwk" });
  const priv = privateKey.export({ format: "jwk" });
  const b = (s: string) => Buffer.from(s, "base64url");
  const point = Buffer.concat([Buffer.from([4]), b(pub.x!), b(pub.y!)]);
  return { VAPID_PUBLIC_KEY: point.toString("base64url"), VAPID_PRIVATE_KEY: priv.d!, VAPID_SUBJECT: "mailto:e2e@example.com" };
}
const VAPID = vapidKeys();

const WEB = 3100;
const API = 8100;

export default defineConfig({
  testDir: "./e2e",
  globalTeardown: "./e2e/global-teardown.ts",
  timeout: 120_000,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["json", { outputFile: "../reports/e2e-results.json" }]],
  use: { baseURL: `http://127.0.0.1:${WEB}`, serviceWorkers: "allow", trace: "retain-on-failure" },
  webServer: [
    {
      // Local backend: real hanja DB + stroke data, fake identity/Toss, ALL state (accounts, learning,
      // conversations, usage counters, notifications) on the embedded PostgreSQL in .pgdata/e2e, which is
      // reset before each run. backend/.env is NOT read (deterministic fake LLM, no cost) unless E2E_REAL_LLM=1;
      // a fresh VAPID key pair enables the Web Push endpoints.
      command: `python -m db.pg reset .pgdata/e2e && python -m uvicorn app.main:app --host 127.0.0.1 --port ${API}`,
      cwd: "../backend",
      url: `http://127.0.0.1:${API}/api/health`,
      env: {
        ADMIN_TOKEN: "e2e-admin", PYTHONPATH: ".", PG_EMBEDDED_DIR: ".pgdata/e2e", ...VAPID,
        ...(process.env.E2E_REAL_LLM === "1" ? {} : { HANJA_SKIP_DOTENV: "1", LLM_CONFIG: "" }),
      },
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `npm run build && npx next start -p ${WEB}`,
      url: `http://127.0.0.1:${WEB}`,
      env: { BACKEND_URL: `http://127.0.0.1:${API}` },
      reuseExistingServer: false,
      timeout: 300_000,
    },
  ],
});
