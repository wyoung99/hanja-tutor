"""Composed API: the same journey the Playwright E2E drives through the browser."""
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import TOP, BOT, create_app  # noqa: E402


def test_full_journey_signup_to_refund_request():
    c = TestClient(create_app(env={"ADMIN_TOKEN": "adm"}))
    tok = c.post("/api/dev/verify", json={"ci": "ci-1"}).json()["verification_token"]
    assert c.post("/api/parents/signup", json={
        "verification_token": tok, "email": "p@x.kr", "agree_terms": True,
        "agree_privacy": True, "agree_legal_guardian": True}).status_code == 201
    tok2 = c.post("/api/dev/verify", json={"ci": "ci-1"}).json()["verification_token"]
    H = {"Authorization": "Bearer " + c.post("/api/parents/login", json={"verification_token": tok2}).json()["session"]}
    kid = c.post("/api/children", headers=H, json={"nickname": "별이", "pin": "1234", "ai_consent": True}).json()["id"]
    assert c.put(f"/api/children/{kid}/target", headers=H, json={"body": "korea_eomunhoe", "grade": "8"}).status_code == 200

    today = c.get(f"/api/children/{kid}/today").json()
    assert 3 <= len(today["new"]) <= 15
    ch = today["new"][0]["char"]

    assert c.post(f"/api/children/{kid}/tracing/{ch}/start").json()["mode"] == "graded"
    r = None
    for s in (TOP, BOT):
        r = c.post(f"/api/children/{kid}/tracing/{ch}/stroke", json={"stroke": [list(p) for p in s]}).json()
    assert r["done"] and r["result"] == "pass"
    assert c.post(f"/api/children/{kid}/today/{ch}/complete").status_code == 200

    ex = c.post(f"/api/children/{kid}/exams", json={"seed": 1}).json()
    q = ex["questions"][0]
    assert c.post(f"/api/exams/{ex['exam_id']}/answer", json={"question_id": q["id"], "value": q["options"][0]}).status_code == 200
    res = c.post(f"/api/exams/{ex['exam_id']}/submit").json()
    assert "passed" in res and res["total"] == ex["total_score"]
    # weekly limit (beta 1) reached for a *new* exam
    assert c.post(f"/api/children/{kid}/exams", json={"seed": 2}).status_code == 429

    chat = c.post(f"/api/children/{kid}/chat", json={"message": "學이 뭐예요?"}).json()
    assert chat["status"] == "answered"

    assert c.get(f"/api/children/{kid}/report/basic", headers=H).status_code == 200
    assert c.get(f"/api/children/{kid}/report/detailed", headers=H).status_code == 402

    plans = c.get("/api/billing/plans").json()
    assert {p["id"] for p in plans} == {"monthly", "yearly"}
    assert c.post("/api/billing/subscribe", headers=H, json={"plan_id": "monthly", "auth_key": "fake"}).status_code == 201
    assert c.get(f"/api/children/{kid}/report/detailed", headers=H).status_code == 200
    rf = c.post("/api/refunds", headers=H)
    assert rf.status_code == 201 and rf.json()["status"] == "auto_rejected"  # used the detailed report
    assert rf.json()["can_request_review"] is True
    assert c.post(f"/api/refunds/{rf.json()['id']}/review", headers=H).json()["status"] == "review_requested"
    assert c.get("/api/admin/refunds?status=review_requested", headers={"X-Admin-Token": "adm"}).status_code == 200


def test_dev_verify_disabled_in_production():
    c = TestClient(create_app(env={"HANJA_ENV": "production"}))
    assert c.post("/api/dev/verify", json={}).status_code == 404
