"""Composed API on the real hanja DB: the same journey the Playwright E2E drives through the browser."""
import re
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auth_helpers as A  # noqa: E402
from app.main import create_app  # noqa: E402
from hanja_db.store import HanjaStore  # noqa: E402

STORE = HanjaStore.load()
FAKE_READING = re.compile(r"^[훈음]\d+$")


def login(c, uid="mom01"):
    return A.parent(c, uid)


def child(c, H, nick, body, grade, consent=True, login_id=None):
    kid = A.new_child(c, H, nick, login_id=login_id, consent=consent)
    r = c.put(f"/api/children/{kid}/target", headers=H, json={"body": body, "grade": grade})
    assert r.status_code == 200, r.text
    return kid


def test_full_journey_signup_to_report():
    c = TestClient(create_app(env={"ADMIN_TOKEN": "adm"}))
    H = A.parent(c, "mom01", default=False)
    kid = child(c, H, "별이", "korea_eomunhoe", "8", login_id="star01")
    c.headers.update(A.child_login(c, "star01"))  # the child logs in on its own device: learning as the child

    today = c.get(f"/api/children/{kid}/today").json()
    assert 3 <= len(today["new"]) <= 15
    for card in today["new"]:  # real per-body readings from hanja_by_body.csv, never synthetic
        r = STORE.reading("korea_eomunhoe", card["char"])
        assert r is not None and (card["hun"], card["eum"]) == (r.hun, r.eum)
        assert not FAKE_READING.match(card["hun"] or "") and card["reviewed"] is r.reviewed  # API mirrors the DB flag
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


def test_jun_grade_mock_exam_uses_the_official_daegeom_format():
    """준5·준4·준3 now have the official format: 50 객관식, 2점씩, 35문항(70점) 합격, 40분."""
    c = TestClient(create_app())
    H = login(c)
    cat = {x["body"]: x for x in c.get("/api/catalog").json()}
    assert cat["daegeom"]["grades"] == ["8", "7", "6", "준5", "5", "준4", "4", "준3", "3"]
    assert all(v["status"] == "available" and v["message"] is None for v in cat["daegeom"]["exam"].values())
    assert all(v["status"] == "available" for v in cat["korea_eomunhoe"]["exam"].values())
    j5 = cat["daegeom"]["exam"]["준5"]
    assert (j5["questions"], j5["pass_questions"], j5["pass_score"], j5["total_score"], j5["minutes"]) == (50, 35, 70, 100, 40)
    assert j5["all_multiple_choice"] and j5["type_mix"] == "app_defined" and j5["notice"] is None
    e8 = cat["daegeom"]["exam"]["8"]
    assert (e8["questions"], e8["points_per_question"], e8["pass_questions"], e8["pass_score"]) == (25, 4, 18, 70)
    k8 = cat["korea_eomunhoe"]["exam"]["8"]
    assert k8["type_mix"] == "partial_substitution" and "대체" in k8["notice"]
    kid = child(c, H, "준이", "daegeom", "준5")
    today = c.get(f"/api/children/{kid}/today").json()
    assert today["exam"]["status"] == "available" and 3 <= len(today["new"]) <= 15
    assert all(STORE.reading("daegeom", x["char"]).grade == "준5" for x in today["new"])
    ex = c.post(f"/api/children/{kid}/exams", json={"seed": 1}).json()
    assert len(ex["questions"]) == 50 and ex["total_score"] == 100 and ex["pass_score"] == 70
    assert ex["minutes"] == 40 and ex["pass_questions"] == 35 and ex["points_per_question"] == 2
    assert all(q["options"] and q["type"] != "writing" and q["points"] == 2 for q in ex["questions"])
    res = c.post(f"/api/exams/{ex['exam_id']}/submit").json()
    assert res["score"] == 0 and res["passed"] is False and res["question_count"] == 50
    assert c.put(f"/api/children/{kid}/target", headers=H, json={"body": "daegeom", "grade": "2"}).status_code == 422


def test_eomunhoe_exam_has_official_type_slots_with_pilsun_figure():
    c = TestClient(create_app())
    H = login(c)
    kid = child(c, H, "필순이", "korea_eomunhoe", "8")
    ex = c.post(f"/api/children/{kid}/exams", json={"seed": 3}).json()
    slots = [q["slot"] for q in ex["questions"]]
    assert (slots.count("dokeum"), slots.count("hunum"), slots.count("pilsun")) == (24, 24, 2)
    for q in ex["questions"]:
        if q["slot"] == "pilsun":
            fig = q["figure"]
            assert 0 <= fig["highlight"] < len(fig["strokes"]) and len(q["options"]) == 4
    assert ex["notice"] and ex["type_mix"] == "partial_substitution"
    res = c.post(f"/api/exams/{ex['exam_id']}/submit").json()
    assert [k for k in res["by_type"]] == ["dokeum", "hunum", "pilsun"]
    assert [v["label"] for v in res["by_type"].values()] == ["讀音", "訓音", "筆順"]
    assert res["data_caveats"]["pilsun"] and res["substitutions"] == {"dokeum": {"count": 24, "with": "eum"}}


def test_billing_routes_are_wired():
    """Payment is implement-only (AC14/15 verification excluded); only check the routes exist."""
    c = TestClient(create_app(env={"ADMIN_TOKEN": "adm"}))
    plans = c.get("/api/billing/plans").json()
    assert {p["id"] for p in plans} == {"monthly", "yearly"}
    assert c.get("/api/admin/refunds?status=review_requested", headers={"X-Admin-Token": "adm"}).status_code == 200


def test_no_phone_verification_or_dev_endpoints_in_production():
    c = TestClient(create_app(env={"HANJA_ENV": "production"}))
    assert c.post("/api/dev/verify", json={}).status_code == 404  # phone verification is gone everywhere
    assert c.post("/api/dev/clock", json={}).status_code == 404
    assert A.signup(c, "prod01").status_code == 201  # sign-up needs no verification step
