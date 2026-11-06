"""Restart survival on real processes: uvicorn #1 writes, uvicorn AND PostgreSQL are stopped, uvicorn #2 reads.

    python backend/scripts/restart_survival.py        -> reports/restart-survival.json

Uses the embedded PostgreSQL in backend/.pgdata/restart-demo (reset first, removed afterwards) and the fake LLM
(backend/.env is not read: no cost). Between the two runs the PostgreSQL server itself is stopped, so the
second server reads everything back from disk.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

from db.pg import reset_embedded_postgres, stop_embedded_postgres  # noqa: E402
from measure_p95 import free_port  # noqa: E402

DATA = BACKEND / ".pgdata" / "restart-demo"


def start(port: int) -> subprocess.Popen:
    env = {**os.environ, "PYTHONPATH": str(BACKEND), "HANJA_SKIP_DOTENV": "1", "LLM_CONFIG": "",
           "PG_EMBEDDED_DIR": str(DATA)}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                            cwd=BACKEND, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(240):
        try:
            if httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=1).status_code == 200:
                return proc
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    proc.kill()
    raise SystemExit("uvicorn did not start")


def stop(proc: subprocess.Popen) -> None:
    proc.terminate()
    proc.wait(timeout=30)


def snapshot(c: httpx.Client, H: dict, cid: str) -> dict:
    return {
        "children": c.get("/api/children", headers=H).json(),
        "plan": c.get(f"/api/children/{cid}/plan", headers=H).json(),
        "today": c.get(f"/api/children/{cid}/today").json(),
        "active_exam": {k: v for k, v in c.get(f"/api/children/{cid}/exams/active").json().items()
                        if k in ("exam_id", "answers", "deadline")},
        "screentime_daily_minutes": c.get(f"/api/children/{cid}/screentime", headers=H).json()["daily_minutes"],
        "chatbot_used_today": c.get(f"/api/children/{cid}/usage").json()["chatbot"]["used"],
        "threads": c.get(f"/api/children/{cid}/threads").json(),
        "safety_events": c.get(f"/api/children/{cid}/safety-events", headers=H).json(),
        "notifications": [(n["seq"], n["kind"]) for n in c.get("/api/parents/me/notifications", headers=H).json()],
        "subscription": c.get("/api/billing/subscription", headers=H).json(),
        "report_basic": c.get(f"/api/children/{cid}/report/basic", headers=H).json(),
    }


def main() -> int:
    reset_embedded_postgres(DATA)
    port = free_port()
    p1 = start(port)
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30) as c:
            c.post("/api/parents/signup", json={"login_id": "restart-demo", "password": "restart-demo-1",
                                                "email": "demo@example.com", "agree_terms": True,
                                                "agree_privacy": True, "agree_legal_guardian": True}).raise_for_status()
            H = {"Authorization": "Bearer " + c.post("/api/auth/login", json={
                "login_id": "restart-demo", "password": "restart-demo-1"}).json()["session"]}
            cid = c.post("/api/children", headers=H, json={"login_id": "restart-kid", "password": "kid1234",
                                                           "nickname": "별이", "ai_consent": True}).json()["id"]
            c.put(f"/api/children/{cid}/target", headers=H, json={"body": "korea_eomunhoe", "grade": "8"}).raise_for_status()
            c.put(f"/api/children/{cid}/plan", headers=H, json={"exam_date": None, "daily_count": 4}).raise_for_status()
            K = {"Authorization": "Bearer " + c.post("/api/auth/login", json={
                "login_id": "restart-kid", "password": "kid1234"}).json()["session"]}
            c.headers.update(K)  # learning calls below: the child's own session
            today = c.get(f"/api/children/{cid}/today").json()
            c.post(f"/api/children/{cid}/today/{today['new'][0]['char']}/complete").raise_for_status()
            ex = c.post(f"/api/children/{cid}/exams", json={"seed": 1}).json()
            q = ex["questions"][0]
            c.post(f"/api/exams/{ex['exam_id']}/answer", json={"question_id": q["id"], "value": q["options"][0]})
            c.put(f"/api/children/{cid}/screentime", headers=H, json={"daily_minutes": 90, "windows": []})
            c.post(f"/api/children/{cid}/chat", json={"message": "學이 뭐예요?"})
            c.post(f"/api/children/{cid}/chat", json={"message": "내 전화번호는 010-1234-5678이야"})
            c.post("/api/billing/subscribe", headers=H, json={"plan_id": "monthly", "auth_key": "ak"}).raise_for_status()
            before = snapshot(c, H, cid)
    finally:
        stop(p1)
    pg_stopped = stop_embedded_postgres(DATA)  # the database server goes down too: data must come from disk

    p2 = start(port)
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30) as c:
            c.headers.update(K)
            after = snapshot(c, H, cid)  # same bearer tokens (parent + child): sessions survive too
            health = c.get("/api/health").json()
    finally:
        stop(p2)
        stop_embedded_postgres(DATA)
    diffs = sorted(k for k in before if before[k] != after[k])
    out = {"measured_at": datetime.now(timezone.utc).isoformat(), "state_store": health.get("state_store"),
           "postgres_restarted_between_runs": pg_stopped, "compared": sorted(before), "differences": diffs,
           "pass": not diffs and health.get("state_store") == "postgres", "before": before, "after": after}
    path = ROOT / "reports" / "restart-survival.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    reset_embedded_postgres(DATA)
    print(json.dumps({k: out[k] for k in ("state_store", "postgres_restarted_between_runs", "differences", "pass")}))
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
