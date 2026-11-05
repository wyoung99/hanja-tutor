"""Composed API on the real hanja DB: the same journey the Playwright E2E drives through the browser."""
import re
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import create_app  # noqa: E402
from hanja_db.store import HanjaStore  # noqa: E402

STORE = HanjaStore.load()
FAKE_READING = re.compile(r"^[훈음]\d+$")


def login(c, ci="ci-1"):
    tok = c.post("/api/dev/verify", json={"ci": ci}).json()["verification_token"]
    assert c.post("/api/parents/signup", json={
        "verification_token": tok, "email": f"{ci}@x.kr", "agree_terms": True,
        "agree_privacy": True, "agree_legal_guardian": True}).status_code == 201
    tok2 = c.post("/api/dev/verify", json={"ci": ci}).json()["verification_token"]
    return {"Authorization": "Bearer " + c.post("/api/parents/login", json={"verification_token": tok2}).json()["session"]}


def child(c, H, nick, body, grade, consent=True):
    kid = c.post("/api/children", headers=H, json={"nickname": nick, "pin": "1234", "ai_consent": consent}).json()["id"]
    r = c.put(f"/api/children/{kid}/target", headers=H, json={"body": body, "grade": grade})
    assert r.status_code == 200, r.text
    return kid


def test_full_journey_signup_to_report():
    c = TestClient(create_app(env={"ADMIN_TOKEN": "adm"}))
    H = login(c)
    kid = child(c, H, "별이", "korea_eomunhoe", "8")

    today = c.get(f"/api/children/{kid}/today").json()
    assert 3 <= len(today["new"]) <= 15
    for card in today["new"]:  # real per-body readings from hanja_by_body.csv, never synthetic
        r = STORE.reading("korea_eomunhoe", card["char"])
        assert r is not None and (card["hun"], card["eum"]) == (r.hun, r.eum)
        assert not FAKE_READING.match(card["hun"] or "") and card["reviewed"] is False
    card = next(x for x in today["new"] if x["tracing"] == "graded")
    ch = card["char"]

    # tracing follows the char's real hanzi-writer medians
    st = c.post(f"/api/children/{kid}/tracing/{ch}/start").json()
    assert st["mode"] == "graded" and st["stroke_count"] == card["strokes"] == len(st["strokes"]) > 0
    assert st["strokes"] == c.get(f"/api/strokes/{ch}").json()["strokes"]
    r = None
    for s in st["strokes"]:
        r = c.post(f"/api/children/{kid}/tracing/{ch}/stroke", json={"stroke": s}).json()
        assert r["ok"], r
    assert r["done"] and r["result"] == "pass"
    assert c.post(f"/api/children/{kid}/today/{ch}/complete").status_code == 200

    ex = c.post(f"/api/children/{kid}/exams", json={"seed": 1}).json()
    assert len(ex["questions"]) == 50 and ex["total_score"] == 50
    q = ex["questions"][0]
    assert c.post(f"/api/exams/{ex['exam_id']}/answer", json={"question_id": q["id"], "value": q["options"][0]}).status_code == 200
    res = c.post(f"/api/exams/{ex['exam_id']}/submit").json()
    assert "passed" in res and res["total"] == ex["total_score"]
    for w in res["wrong"]:  # explanations carry the body's DB reading
        rd = STORE.reading("korea_eomunhoe", w["char"])
        assert rd.hun_eum in w["explanation"]
    # weekly limit (beta 1) reached for a *new* exam
    assert c.post(f"/api/children/{kid}/exams", json={"seed": 2}).status_code == 429

    chat = c.post(f"/api/children/{kid}/chat", json={"message": "學이 뭐예요?"}).json()
    assert chat["status"] == "answered"

    assert c.get(f"/api/children/{kid}/report/basic", headers=H).status_code == 200
    assert c.get(f"/api/children/{kid}/report/detailed", headers=H).status_code == 402


def test_char_without_stroke_data_is_not_a_tracing_target():
    c = TestClient(create_app())
    H = login(c)
    kid = child(c, H, "별이", "korea_eomunhoe", "8")
    assert "敎" in STORE.grade_chars("korea_eomunhoe", "8")
    assert c.get(f"/api/children/{kid}/hanja/敎").json()["tracing"] == "unavailable"
    assert c.post(f"/api/children/{kid}/tracing/敎/start").json()["mode"] == "unavailable"
    assert c.get("/api/strokes/敎").status_code == 404


def test_hun_eum_shown_per_selected_body():
    c = TestClient(create_app())
    H = login(c)
    a = child(c, H, "가", "korea_eomunhoe", "8")
    b = child(c, H, "나", "daegeom", "8")
    ra = c.get(f"/api/children/{a}/hanja/兄").json()
    rb = c.get(f"/api/children/{b}/hanja/兄").json()
    assert (ra["hun"], ra["eum"]) == ("형", "형")
    assert (rb["hun"], rb["eum"]) == ("맏", "형")
    # compatibility code point in the URL resolves via NFC (金 U+F90A -> U+91D1)
    assert c.get(f"/api/children/{a}/hanja/金").json()["char"] == "金"
    # a char outside the selected body's DB is not borrowed from the other body
    only_eomunhoe = next(ch for ch in STORE.cumulative_chars("korea_eomunhoe", "4")
                         if STORE.reading("daegeom", ch) is None)
    assert c.get(f"/api/children/{b}/hanja/{only_eomunhoe}").status_code == 404


def test_jun_grade_learning_works_but_mock_exam_is_blocked():
    c = TestClient(create_app())
    H = login(c)
    cat = {x["body"]: x for x in c.get("/api/catalog").json()}
    assert cat["daegeom"]["grades"] == ["8", "7", "6", "준5", "5", "준4", "4", "준3", "3"]
    assert cat["daegeom"]["exam"]["준5"] == {"status": "format_pending", "message": "형식 준비 중"}
    assert cat["daegeom"]["exam"]["5"]["status"] == "available"
    kid = child(c, H, "준이", "daegeom", "준5")
    today = c.get(f"/api/children/{kid}/today").json()
    assert today["exam"]["status"] == "format_pending" and 3 <= len(today["new"]) <= 15
    assert all(STORE.reading("daegeom", x["char"]).grade == "준5" for x in today["new"])
    before = c.get(f"/api/children/{kid}/usage").json()["mockexam"]
    r = c.post(f"/api/children/{kid}/exams", json={"seed": 1})
    assert r.status_code == 409
    assert r.json()["detail"] == {"code": "exam_format_pending", "message": "형식 준비 중"}
    assert c.get(f"/api/children/{kid}/usage").json()["mockexam"] == before  # nothing deducted
    assert c.put(f"/api/children/{kid}/target", headers=H, json={"body": "daegeom", "grade": "2"}).status_code == 422


def test_billing_routes_are_wired():
    """Payment is implement-only (AC14/15 verification excluded); only check the routes exist."""
    c = TestClient(create_app(env={"ADMIN_TOKEN": "adm"}))
    plans = c.get("/api/billing/plans").json()
    assert {p["id"] for p in plans} == {"monthly", "yearly"}
    assert c.get("/api/admin/refunds?status=review_requested", headers={"X-Admin-Token": "adm"}).status_code == 200


def test_dev_verify_disabled_in_production():
    c = TestClient(create_app(env={"HANJA_ENV": "production"}))
    assert c.post("/api/dev/verify", json={}).status_code == 404
