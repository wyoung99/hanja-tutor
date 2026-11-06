"""AC9: local first-token latency (p95) of the streaming chatbot, written to reports/p95.json.

Default: starts a local uvicorn (backend/.env is loaded by the app -> real Bedrock when LLM_CONFIG and AWS
keys are set), signs up a test parent/child through the dev endpoints and sends N vocabulary questions to
POST /api/children/{id}/chat/stream. "First token" = time from sending the request until the first
``event: token`` frame arrives (the server streams DB-corrected sentences, so this includes the first
sentence). Use --url to measure an already running server instead (e.g. next start on :3100 to include
the Next.js proxy hop).

    python backend/scripts/measure_p95.py --n 20
    python backend/scripts/measure_p95.py --url http://127.0.0.1:3100 --n 20

The result is never invented: if the server runs the fake LLM the file says so and "valid_for_ac9" is false.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
SLO_SECONDS = 3.0
QUESTIONS = [
    "學이 무슨 뜻이에요?", "校는 무슨 뜻이에요?", "山은 어떤 한자예요?", "水가 들어간 낱말 알려 주세요.",
    "火는 무슨 뜻이에요?", "木으로 만든 낱말이 뭐가 있어요?", "金은 무슨 뜻이에요?", "土는 어떻게 읽어요?",
    "日과 月은 뭐가 달라요?", "人이 들어간 낱말 알려 줘요.", "大는 무슨 뜻이에요?", "小는 어떻게 읽어요?",
    "中은 무슨 뜻이에요?", "上과 下를 알려 주세요.", "東은 무슨 뜻이에요?", "西는 어떻게 읽어요?",
    "南은 무슨 뜻이에요?", "北은 무슨 뜻이에요?", "先生은 무슨 뜻이에요?", "學校는 무슨 뜻이에요?",
    "父母는 무슨 뜻이에요?", "兄弟는 무슨 뜻이에요?", "年은 무슨 뜻이에요?", "門은 무슨 뜻이에요?",
]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def p95(xs):
    ys = sorted(xs)
    return ys[min(len(ys) - 1, max(0, -(-95 * len(ys) // 100) - 1))] if ys else None


def start_server(port: int) -> subprocess.Popen:
    env = {k: v for k, v in os.environ.items() if k != "HANJA_SKIP_DOTENV"}
    env.update({"PYTHONPATH": str(BACKEND), "USAGE_CHATBOT_DAILY_LIMIT": "100000"})
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                             "--port", str(port)], cwd=BACKEND, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(120):
        try:
            if httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=1).status_code == 200:
                return proc
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    proc.kill()
    raise SystemExit("uvicorn did not start")


def hedge_delta(before: dict, after: dict) -> dict:
    """Hedge counters (from /api/health llm.hedge) for the measured requests only: per task calls / hedged
    (= extra provider calls) / hedge_won, plus the totals."""
    out, tot = {}, {"calls": 0, "hedged": 0, "hedge_won": 0}
    for task, a in (after or {}).items():
        b = (before or {}).get(task, {})
        d = {k: a.get(k, 0) - b.get(k, 0) for k in tot}
        out[task] = {"hedge_after_s": a.get("hedge_after_s"), **d}
        for k in tot:
            tot[k] += d[k]
    out["total"] = {**tot, "extra_calls": tot["hedged"]}
    return out


def setup_child(c: httpx.Client) -> str:
    """Parent signs up (id + password), creates the child account, then the CHILD logs in: the measured chat
    requests carry the child's session (client default header)."""
    uid = f"p95{time.time_ns() % 10**12}"
    c.post("/api/parents/signup", json={"login_id": uid, "password": "measure-p95-1", "email": f"{uid}@example.com",
                                        "agree_terms": True, "agree_privacy": True, "agree_legal_guardian": True}
           ).raise_for_status()
    login = c.post("/api/auth/login", json={"login_id": uid, "password": "measure-p95-1"}).json()
    H = {"Authorization": "Bearer " + login["session"]}
    cid = c.post("/api/children", headers=H, json={"login_id": uid + "k", "password": "kid1234", "nickname": "측정",
                                                   "ai_consent": True}).json()["id"]
    c.put(f"/api/children/{cid}/target", headers=H, json={"body": "korea_eomunhoe", "grade": "8"}).raise_for_status()
    kid = c.post("/api/auth/login", json={"login_id": uid + "k", "password": "kid1234"}).json()
    c.headers["Authorization"] = "Bearer " + kid["session"]
    return cid


def ask(c: httpx.Client, cid: str, q: str) -> dict:
    t0 = time.perf_counter()
    first, status, kinds = None, None, []
    with c.stream("POST", f"/api/children/{cid}/chat/stream", json={"message": q}, timeout=60) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if line.startswith("event: "):
                kinds.append(line[7:])
                if line == "event: token" and first is None:
                    first = time.perf_counter() - t0
            elif line.startswith("data: ") and kinds and kinds[-1] in ("done", "error"):
                status = json.loads(line[6:]).get("status")
    return {"question": q, "first_token_s": None if first is None else round(first, 3),
            "total_s": round(time.perf_counter() - t0, 3), "status": status, "events": len(kinds)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="measure a running server instead of starting uvicorn")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--out", default=str(ROOT / "reports" / "p95.json"))
    a = ap.parse_args()
    proc = None
    url = a.url
    if not url:
        port = free_port()
        proc = start_server(port)
        url = f"http://127.0.0.1:{port}"
    try:
        with httpx.Client(base_url=url, timeout=30) as c:
            health = c.get("/api/health").json()
            cid = setup_child(c)
            ask(c, cid, "一은 무슨 뜻이에요?")  # warm-up (connection + model client init), not counted
            hedge0 = (c.get("/api/health").json().get("llm") or {}).get("hedge") or {}
            samples = [ask(c, cid, QUESTIONS[i % len(QUESTIONS)]) for i in range(a.n)]
            hedge1 = (c.get("/api/health").json().get("llm") or {}).get("hedge") or {}
    finally:
        if proc:
            proc.terminate()
    ft = [s["first_token_s"] for s in samples if s["first_token_s"] is not None]
    real = (health.get("llm") or {}).get("chat") == "BedrockProvider"
    out = {
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "environment": "local " + ("uvicorn (started by script)" if proc else url),
        "target": url, "llm": health.get("llm"), "real_bedrock": real,
        "sample_size": len(samples), "with_first_token": len(ft),
        "first_token_p50_s": sorted(ft)[len(ft) // 2] if ft else None,
        "first_token_p95_s": p95(ft), "first_token_max_s": max(ft) if ft else None,
        "slo_s": SLO_SECONDS,
        "errors": sum(1 for s in samples if s["status"] not in ("answered", "unknown")),
        "pass": bool(ft) and len(ft) == len(samples) and p95(ft) < SLO_SECONDS,
        "valid_for_ac9": real,
        "hedge": hedge_delta(hedge0, hedge1),
        "note": "first token = first DB-corrected sentence frame (event: token) after the request was sent",
        "samples": samples,
    }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("real_bedrock", "sample_size", "first_token_p50_s",
                                          "first_token_p95_s", "first_token_max_s", "errors", "pass", "hedge")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
