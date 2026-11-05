import { execFileSync } from "node:child_process";
import { resolve } from "node:path";

/** The E2E backend's embedded PostgreSQL outlives the killed uvicorn process; stop it after the run. */
export default function globalTeardown() {
  try {
    execFileSync("python", ["-m", "db.pg", "stop", ".pgdata/e2e"], { cwd: resolve(__dirname, "../../backend"), stdio: "inherit" });
  } catch {
    // best effort: a still-running server is reused by the next run
  }
}
