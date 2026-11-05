import { defineConfig } from "@playwright/test";

const WEB = 3100;
const API = 8100;

export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: { baseURL: `http://127.0.0.1:${WEB}`, serviceWorkers: "allow", trace: "retain-on-failure" },
  webServer: [
    {
      // Staging backend: demo hanja world, fake identity/Toss/LLM, fresh in-memory state per run.
      command: `python -m uvicorn app.main:app --host 127.0.0.1 --port ${API}`,
      cwd: "../backend",
      url: `http://127.0.0.1:${API}/api/health`,
      env: { ADMIN_TOKEN: "e2e-admin", PYTHONPATH: "." },
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
