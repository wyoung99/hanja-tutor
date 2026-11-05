import { defineConfig } from "@playwright/test";

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
      // Local backend: real hanja DB + stroke data, fake identity/Toss, conversations on the embedded
      // PostgreSQL (PostgresSaver + chat_threads). backend/.env is NOT read (deterministic fake LLM, no cost)
      // unless E2E_REAL_LLM=1. Account state is in memory, so every run starts fresh.
      command: `python -m uvicorn app.main:app --host 127.0.0.1 --port ${API}`,
      cwd: "../backend",
      url: `http://127.0.0.1:${API}/api/health`,
      env: {
        ADMIN_TOKEN: "e2e-admin", PYTHONPATH: ".", PG_EMBEDDED_DIR: ".pgdata/e2e",
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
