"""End-to-end wiring of the composed API (app/main.py): every service is reachable over HTTP.

Covers the routes added for AC2-AC12 on the real hanja DB with an injected KST clock.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import Clock, create_app
from chatbot.dictionary import UrimalsaemClient
from hanja_db.store import HanjaStore
from llm import FakeProvider, LLMError, LLMRouter

KST = timezone(timedelta(hours=9))
T0 = datetime(2026, 11, 5, 10, 0, tzinfo=KST)  # Thursday
STORE = HanjaStore.load()


def make(env=None, t=T0):
    clock = Clock(t)
    app = create_app(clock=clock, env={"ADMIN_TOKEN": "adm", **(env or {})})
    return TestClient(app), clock, app.state.services


def login(c, ci="ci-1"):
    tok = c.post("/api/dev/verify", json={"ci": ci}).json()["verification_token"]
    c.post("/api/parents/signup", json={"verification_token": tok, "email": f"{ci}@x.kr", "agree_terms": True,
                                        "agree_privacy": True, "agree_legal_guardian": True})
    tok2 = c.post("/api/dev/verify", json={"ci": ci}).json()["verification_token"]
    return {"Authorization": "Bearer " + c.post("/api/parents/login", json={"verification_token": tok2}).json()["session"]}


def kid(c, H, nick="별이", body="korea_eomunhoe", grade="8", consent=True):
    r = c.post("/api/children", headers=H, json={"nickname": nick, "pin": "1234", "ai_consent": consent})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert c.put(f"/api/children/{cid}/target", headers=H, json={"body": body, "grade": grade}).status_code == 200
    return cid


# ---- AC2 -------------------------------------------------------------------------------
def test_consent_toggle_and_pin_lock_reset_over_http():
    c, _, _ = make()
    H = login(c)
    cid = kid(c, H, consent=False)
    assert c.post(f"/api/children/{cid}/chat", json={"message": "學이 뭐예요?"}).status_code == 403
    assert c.post(f"/api/children/{cid}/chat/stream", json={"message": "學이 뭐예요?"}).status_code == 403
    assert c.get(f"/api/children/{cid}/today").status_code == 200  # other learning still works
    assert c.put(f"/api/children/{cid}/ai-consent", headers=H, json={"ai_consent": True}).status_code == 200
    assert c.post(f"/api/children/{cid}/chat", json={"message": "學이 뭐예요?"}).json()["status"] == "answered"

    for _ in range(4):
        assert c.post(f"/api/children/{cid}/verify-pin", json={"pin": "0000"}).status_code == 401
    assert c.post(f"/api/children/{cid}/verify-pin", json={"pin": "0000"}).status_code == 423
    assert c.post(f"/api/children/{cid}/verify-pin", json={"pin": "1234"}).status_code == 423
    assert c.get("/api/children", headers=H).json()[0]["locked"] is True
    assert c.post(f"/api/children/{cid}/reset-pin", json={"pin": "5678"}).status_code in (401, 403)  # parent only
    assert c.post(f"/api/children/{cid}/reset-pin", headers=H, json={"pin": "5678"}).status_code == 200
    assert c.post(f"/api/children/{cid}/verify-pin", json={"pin": "5678"}).status_code == 200


# ---- AC3 -------------------------------------------------------------------------------
def test_delete_child_leaves_zero_learning_records_and_conversations():
    c, _, svc = make()
    H = login(c)
    cid = kid(c, H)
    other = kid(c, H, "달이")
    today = c.get(f"/api/children/{cid}/today").json()
    ch = today["new"][0]["char"]
    c.post(f"/api/children/{cid}/today/{ch}/complete")
    c.post(f"/api/children/{cid}/tracing/{ch}/start")
    c.post(f"/api/children/{cid}/exams", json={"seed": 1})
    c.post(f"/api/children/{cid}/chat", json={"message": "學이 뭐예요?"})
    c.post(f"/api/children/{cid}/chat", json={"message": "씨발"})  # safety event
    c.post(f"/api/children/{cid}/heartbeat")
    assert svc["learning_records"](cid) > 0
    r = c.delete(f"/api/children/{cid}", headers=H)
    assert r.status_code == 200 and r.json()["remaining_learning_records"] == 0
    assert svc["learning_records"](cid) == 0 and cid not in svc["acc"].children
    assert svc["conv"].meta.for_child(cid) == [] and svc["conv"].checkpointer.thread_ids() == []
    assert c.get(f"/api/children/{cid}/today").status_code == 404
    assert c.get(f"/api/children/{other}/today").status_code == 200  # sibling untouched


def test_delete_account_archives_payments_separately():
    c, _, svc = make()
    H = login(c)
    cid = kid(c, H)
    assert c.post("/api/billing/subscribe", headers=H, json={"plan_id": "monthly", "auth_key": "k"}).status_code == 201
    c.post(f"/api/children/{cid}/chat", json={"message": "學이 뭐예요?"})
    pid = svc["acc"].children[cid].parent_id
    r = c.delete("/api/parents/me", headers=H)
    assert r.status_code == 200 and r.json()["remaining_learning_records"] == 0
    assert r.json()["archived_payments"] == 1
    assert svc["bill"].payments == [] and pid not in svc["bill"].subs
    arch = svc["life"].archive.rows[0]
    assert arch.record.parent_id == pid and arch.delete_after.year == arch.archived_at.year + 5
    assert c.get("/api/children", headers=H).status_code == 401


def test_dormancy_sweep_job_notice_then_dormant():
    c, clock, svc = make()
    H = login(c)
    kid(c, H)
    assert c.post("/api/admin/jobs/dormancy").status_code == 403
    clock.t = T0 + timedelta(days=336)
    out = c.post("/api/admin/jobs/dormancy", headers={"X-Admin-Token": "adm"}).json()
    assert len(out["notified"]) == 1 and out["dormant"] == []
    assert any(k == "dormancy_notice" for _, k, _ in svc["push"].sent)
    clock.t = T0 + timedelta(days=366)
    out = c.post("/api/admin/jobs/dormancy", headers={"X-Admin-Token": "adm"}).json()
    assert len(out["dormant"]) == 1 and svc["acc"].children == {}
    # login restores (identity re-verification)
    tok = c.post("/api/dev/verify", json={"ci": "ci-1"}).json()["verification_token"]
    assert c.post("/api/parents/login", json={"verification_token": tok}).status_code == 200
    assert len(svc["acc"].children) == 1


# ---- AC4 -------------------------------------------------------------------------------
def test_parent_plan_edit_and_exam_date_drive_today():
    c, clock, _ = make()
    H = login(c)
    cid = kid(c, H, grade="7")
    p = c.get(f"/api/children/{cid}/plan", headers=H).json()
    assert p["exam_date"] is None and 3 <= p["today_quota"] <= 15
    assert c.get(f"/api/children/{cid}/plan").status_code == 401
    r = c.put(f"/api/children/{cid}/plan", headers=H, json={"daily_count": 7})
    assert r.status_code == 200 and r.json()["today_quota"] == 7
    assert len(c.get(f"/api/children/{cid}/today").json()["new"]) == 7
    assert c.put(f"/api/children/{cid}/plan", headers=H, json={"daily_count": 20}).status_code == 422
    assert c.put(f"/api/children/{cid}/plan", headers=H, json={"exam_date": "2026-01-01"}).status_code == 422
    remaining = p["remaining"]
    r = c.put(f"/api/children/{cid}/plan", headers=H, json={"exam_date": "2026-11-14"}).json()  # 10 days
    assert r["exam_date"] == "2026-11-14" and r["daily_count"] is None
    assert r["today_quota"] == min(15, max(3, -(-remaining // 10)))
    # KST midnight -> next day's portion
    first = [x["char"] for x in c.get(f"/api/children/{cid}/today").json()["new"]]
    clock.t = T0.replace(hour=23, minute=59)
    assert [x["char"] for x in c.get(f"/api/children/{cid}/today").json()["new"]] == first
    clock.t = T0 + timedelta(days=1) - timedelta(hours=10)  # 00:00 KST next day
    again = [x["char"] for x in c.get(f"/api/children/{cid}/today").json()["new"]]
    assert again[: len(first)] == first  # nothing learned yet: same chars first, plan recomputed
    for ch in first:
        c.post(f"/api/children/{cid}/today/{ch}/complete")
    clock.t = T0 + timedelta(days=2) - timedelta(hours=10)
    nxt = [x["char"] for x in c.get(f"/api/children/{cid}/today").json()["new"]]
    assert not set(nxt) & set(first)


# ---- AC5 -------------------------------------------------------------------------------
@pytest.mark.parametrize("body,grade", [("korea_eomunhoe", "7"), ("korea_eomunhoe", "6II"), ("daegeom", "6"),
                                        ("daegeom", "5")])
def test_wrong_answers_on_lower_grade_chars_enter_review_without_500(body, grade):
    c, clock, svc = make()
    H = login(c)
    cid = kid(c, H, body=body, grade=grade)
    ex = c.post(f"/api/children/{cid}/exams", json={"seed": 3}).json()
    r = c.post(f"/api/exams/{ex['exam_id']}/submit")  # everything unanswered -> all wrong
    assert r.status_code == 200, r.text
    wrong = {w["char"] for w in r.json()["wrong"]}
    own = set(STORE.grade_chars(body, grade))
    assert wrong - own, "pool is cumulative: some wrong chars belong to lower grades"
    planner = svc["planners"][cid]
    assert wrong <= set(planner.reviews)
    clock.t = T0 + timedelta(days=1)
    due = {x["char"] for x in c.get(f"/api/children/{cid}/today").json()["reviews"]}
    assert wrong <= due
    low = next(iter(wrong - own))
    card = c.get(f"/api/children/{cid}/hanja/{low}").json()
    r = STORE.reading(body, low)
    assert (card["hun"], card["eum"]) == (r.hun or None, r.eum or None)  # the selected body's reading
    assert c.post(f"/api/children/{cid}/reviews/{low}", json={"correct": True}).status_code == 200


# ---- AC6 -------------------------------------------------------------------------------
def test_exam_resume_timer_and_auto_submit_at_deadline():
    c, clock, svc = make()
    H = login(c)
    cid = kid(c, H)
    ex = c.post(f"/api/children/{cid}/exams", json={"seed": 1}).json()
    q = ex["questions"][0]
    c.post(f"/api/exams/{ex['exam_id']}/answer", json={"question_id": q["id"], "value": q["options"][0]})
    assert ex["remaining_seconds"] == ex["minutes"] * 60
    clock.t = T0 + timedelta(minutes=10)  # the child left the app; the timer kept running
    act = c.get(f"/api/children/{cid}/exams/active").json()
    assert act["exam_id"] == ex["exam_id"] and act["answers"] == {q["id"]: q["options"][0]}
    assert act["remaining_seconds"] == (ex["minutes"] - 10) * 60
    again = c.post(f"/api/children/{cid}/exams", json={}).json()  # resume, no second charge
    assert again["exam_id"] == ex["exam_id"]
    assert c.get(f"/api/children/{cid}/usage").json()["mockexam"]["used"] == 1
    clock.t = T0 + timedelta(minutes=ex["minutes"], seconds=1)
    v = c.get(f"/api/exams/{ex['exam_id']}").json()
    assert v["submitted"] and v["remaining_seconds"] == 0 and v["result"]["auto_submitted"] is True
    res = v["result"]
    assert set(res["by_type"]) == {"eum", "hun_eum"}
    assert all("explanation" in w and w["explanation"] for w in res["wrong"])
    assert c.get(f"/api/children/{cid}/exams/active").status_code == 404
    assert c.get(f"/api/children/{cid}/usage").json()["mockexam"]["used"] == 1


def test_dev_clock_shift_only_outside_production():
    c, _, _ = make(t=None)
    before = datetime.fromisoformat(c.post("/api/dev/clock", json={"advance_minutes": 0}).json()["now"])
    after = datetime.fromisoformat(c.post("/api/dev/clock", json={"advance_minutes": 61}).json()["now"])
    assert timedelta(minutes=60) < after - before < timedelta(minutes=62)
    p = TestClient(create_app(env={"HANJA_ENV": "production"}))
    assert p.post("/api/dev/clock", json={"advance_minutes": 1}).status_code in (404, 405)


# ---- AC7 -------------------------------------------------------------------------------
def test_stroke_tolerances_come_from_settings():
    c, _, _ = make()
    assert c.get("/api/tracing/tolerance").json()["position"] == 350.0
    c2, _, svc = make({"STROKE_TOL_POSITION": "120", "STROKE_TOL_DIRECTION_DEG": "45"})
    tol = c2.get("/api/tracing/tolerance").json()
    assert tol["position"] == 120.0 and tol["direction_max_angle_deg"] == 45.0
    assert svc["exam"].tol.position == 120.0
    with pytest.raises(ValueError):
        make({"STROKE_TOL_LENGTH_MIN": "3"})


def test_wrong_stroke_is_flagged_for_highlight_then_revealed():
    c, _, _ = make()
    H = login(c)
    cid = kid(c, H)
    ch = next(x["char"] for x in c.get(f"/api/children/{cid}/today").json()["new"] if x["tracing"] == "graded")
    st = c.post(f"/api/children/{cid}/tracing/{ch}/start").json()
    bad = [[p[0], p[1]] for p in reversed(st["strokes"][0])]  # reversed direction
    for i in range(3):
        r = c.post(f"/api/children/{cid}/tracing/{ch}/stroke", json={"stroke": bad}).json()
        assert r["ok"] is False and r["highlight_stroke"] is True and r["stroke_index"] == 0
    assert r["reveal_answer"] and r["answer_stroke"] == st["strokes"][0]


# ---- AC8 -------------------------------------------------------------------------------
def test_urimalsaem_client_wired_from_env(monkeypatch):
    seen = []

    def fake_get(self, url):
        seen.append(url)
        return '{"channel": {"item": [{"sense": {"definition": "풀이"}}]}}'

    monkeypatch.setattr(UrimalsaemClient, "_default_get", fake_get)
    c, _, svc = make({"OPENDICT_API_KEY": "test-key"})
    assert isinstance(svc["dictionary"], UrimalsaemClient)
    assert c.get("/api/health").json()["dictionary"] == "urimalsaem"
    H = login(c)
    cid = kid(c, H)
    r = c.post(f"/api/children/{cid}/chat", json={"message": "龘이 뭐예요?"}).json()  # not in the DB
    assert r["status"] == "answered" and "우리말샘" in r["reply"] and "풀이" in r["reply"]
    assert seen and "key=test-key" in seen[0] and "num=10" in seen[0]  # the API rejects num < 10


def test_without_dictionary_key_unknown_hanja_is_dont_know():
    c, _, svc = make()
    assert svc["dictionary"] is None
    H = login(c)
    cid = kid(c, H)
    r = c.post(f"/api/children/{cid}/chat", json={"message": "龘이 뭐예요?"}).json()
    assert r["status"] == "unknown" and r["reply"] == "잘 모르겠어요."


def test_parent_sees_safety_events_without_raw_text():
    c, _, svc = make()
    H = login(c)
    cid = kid(c, H)
    c.post(f"/api/children/{cid}/chat", json={"message": "내 전화번호는 010-1234-5678이야"})
    ev = c.get(f"/api/children/{cid}/safety-events", headers=H).json()
    assert len(ev) == 1 and ev[0]["category"] and ev[0]["parent_notified"] is True
    assert not any(f in str(ev) for f in ("010-1234-5678", "01012345678", "1234-5678", "010-1234"))
    notes = c.get("/api/parents/me/notifications", headers=H).json()
    assert any(n["kind"] == "safety_event" for n in notes)
    assert c.get(f"/api/children/{cid}/safety-events").status_code == 401
    H2 = login(c, "ci-2")
    assert c.get(f"/api/children/{cid}/safety-events", headers=H2).status_code == 404


# ---- AC9 -------------------------------------------------------------------------------
def sse_events(resp):
    import json

    return [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]


def test_sse_stream_route_thread_tokens_done_and_resume():
    c, _, svc = make()
    H = login(c)
    cid = kid(c, H)
    r = c.post(f"/api/children/{cid}/chat/stream", json={"message": "學이 뭐예요?"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    ev = sse_events(r)
    assert ev[0]["type"] == "thread" and ev[1]["type"] == "token" and ev[-1]["type"] == "done"
    tid = ev[0]["thread_id"]
    assert c.get(f"/api/children/{cid}/usage").json()["chatbot"]["used"] == 1
    msgs = c.get(f"/api/children/{cid}/threads/{tid}").json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    ev2 = sse_events(c.post(f"/api/children/{cid}/chat/stream", json={"message": "學校는요?", "thread_id": tid}))
    assert ev2[0]["thread_id"] == tid
    llm = svc["bot"].llm
    assert llm.calls[-1][1][0]["content"] == "學이 뭐예요?"  # earlier context sent with the follow-up


def test_sse_error_frame_and_failed_answer_not_counted():
    c, _, svc = make()
    H = login(c)
    cid = kid(c, H)

    def boom(s, m):
        raise LLMError("bedrock down")

    svc["bot"].llm = FakeProvider(boom)
    ev = sse_events(c.post(f"/api/children/{cid}/chat/stream", json={"message": "學이 뭐예요?"}))
    assert ev[-1]["type"] == "error" and ev[-1]["retry"] is True
    assert c.get(f"/api/children/{cid}/usage").json()["chatbot"]["used"] == 0
    assert c.post(f"/api/children/{cid}/chat/stream",
                  json={"message": "x", "thread_id": "nope"}).status_code == 404


# ---- AC10 ------------------------------------------------------------------------------
def test_threads_list_resume_and_delete_everywhere():
    c, _, svc = make()
    H = login(c)
    cid = kid(c, H)
    a = c.post(f"/api/children/{cid}/chat", json={"message": "學이 뭐예요?"}).json()["thread_id"]
    b = c.post(f"/api/children/{cid}/chat", json={"message": "校는 뭐예요?"}).json()["thread_id"]
    assert {t["thread_id"] for t in c.get(f"/api/children/{cid}/threads").json()} == {a, b}
    assert {t["thread_id"] for t in c.get(f"/api/children/{cid}/conversations", headers=H).json()} == {a, b}
    assert c.delete(f"/api/children/{cid}/threads/{a}").status_code == 200
    assert c.delete(f"/api/children/{cid}/conversations/{b}", headers=H).status_code == 200
    assert c.get(f"/api/children/{cid}/threads").json() == []
    assert c.get(f"/api/children/{cid}/conversations", headers=H).json() == []
    assert c.get(f"/api/children/{cid}/threads/{a}").status_code == 404
    assert c.post(f"/api/children/{cid}/chat", json={"message": "x", "thread_id": b}).status_code == 404
    assert svc["conv"].checkpointer.thread_ids() == []


# ---- AC11 ------------------------------------------------------------------------------
def test_screentime_limit_warning_lock_extra_time_and_extension_request():
    c, clock, svc = make()
    H = login(c)
    cid = kid(c, H)
    assert c.put(f"/api/children/{cid}/screentime", headers=H, json={"daily_minutes": 10}).status_code == 200
    assert c.put(f"/api/children/{cid}/screentime", json={"daily_minutes": 10}).status_code == 401
    c.post(f"/api/children/{cid}/heartbeat")
    for m in range(1, 7):  # 6 minutes of use
        clock.t = T0 + timedelta(minutes=m)
        s = c.post(f"/api/children/{cid}/heartbeat").json()
    assert s["warning"] is True and s["locked"] is False  # 5-minute notice
    for m in range(7, 11):
        clock.t = T0 + timedelta(minutes=m)
        s = c.post(f"/api/children/{cid}/heartbeat").json()
    assert s["locked"] is True and s["reason"] == "daily_limit"
    assert c.get(f"/api/children/{cid}/today").json()["detail"]["code"] == "app_locked"
    assert c.post(f"/api/children/{cid}/chat", json={"message": "學"}).status_code == 423
    # extension request -> parent push -> approve
    rq = c.post(f"/api/children/{cid}/extension-requests", json={"minutes": 15}).json()
    assert any(n["kind"] == "extension_request" for n in c.get("/api/parents/me/notifications", headers=H).json())
    pend = c.get("/api/parents/me/extension-requests", headers=H).json()
    assert [p["id"] for p in pend] == [rq["id"]]
    assert c.post(f"/api/parents/me/extension-requests/{rq['id']}", headers=H, json={"approve": True}).json()["status"] == "approved"
    assert c.get(f"/api/children/{cid}/extension-requests/{rq['id']}").json()["status"] == "approved"
    assert c.get(f"/api/children/{cid}/screentime/status").json()["locked"] is False
    assert c.get(f"/api/children/{cid}/today").status_code == 200
    # parent PIN on the child's device grants extra time
    assert c.put("/api/parents/me/pin", headers=H, json={"pin": "9999"}).status_code == 200
    assert c.post(f"/api/children/{cid}/screentime/extra", json={"parent_pin": "0000", "minutes": 10}).status_code == 403
    assert c.post(f"/api/children/{cid}/screentime/extra", json={"parent_pin": "9999", "minutes": 10}).status_code == 200
    # rejection path
    clock.t = T0 + timedelta(minutes=60)
    for m in range(61, 100):
        clock.t = T0 + timedelta(minutes=m)
        c.post(f"/api/children/{cid}/heartbeat")
    rq2 = c.post(f"/api/children/{cid}/extension-requests", json={"minutes": 5}).json()
    c.post(f"/api/parents/me/extension-requests/{rq2['id']}", headers=H, json={"approve": False})
    assert c.get(f"/api/children/{cid}/extension-requests/{rq2['id']}").json()["status"] == "rejected"


def test_lock_waits_for_running_exam():
    c, clock, _ = make()
    H = login(c)
    cid = kid(c, H)
    c.put(f"/api/children/{cid}/screentime", headers=H, json={"windows": [["10:00", "10:30"]]})
    ex = c.post(f"/api/children/{cid}/exams", json={"seed": 1}).json()
    clock.t = T0 + timedelta(minutes=31)
    s = c.post(f"/api/children/{cid}/heartbeat").json()
    assert s["locked"] is False and s["lock_pending"] is True
    q = ex["questions"][0]
    assert c.post(f"/api/exams/{ex['exam_id']}/answer", json={"question_id": q["id"], "value": "x"}).status_code == 200
    c.post(f"/api/exams/{ex['exam_id']}/submit")
    assert c.post(f"/api/children/{cid}/heartbeat").json()["locked"] is True


# ---- AC12 ------------------------------------------------------------------------------
def test_weekly_summary_job_only_for_subscribers():
    c, clock, svc = make()
    Hs = login(c, "ci-sub")
    kid(c, Hs, "구독이")
    c.post("/api/billing/subscribe", headers=Hs, json={"plan_id": "monthly", "auth_key": "k"})
    Hf = login(c, "ci-free")
    kid(c, Hf, "무료")
    assert c.post("/api/admin/jobs/weekly-summary").status_code == 403
    sent = c.post("/api/admin/jobs/weekly-summary", headers={"X-Admin-Token": "adm"}).json()["sent"]
    assert len(sent) == 1
    sub_notes = c.get("/api/parents/me/notifications", headers=Hs).json()
    assert any(n["kind"] == "weekly_summary" for n in sub_notes)
    assert not any(n["kind"] == "weekly_summary" for n in c.get("/api/parents/me/notifications", headers=Hf).json())
    assert c.post("/api/admin/jobs/weekly-summary", headers={"X-Admin-Token": "adm"}).json()["sent"] == []  # once/week


def test_conversation_viewing_is_free_for_every_parent():
    c, _, _ = make()
    H = login(c)
    cid = kid(c, H)
    tid = c.post(f"/api/children/{cid}/chat", json={"message": "學이 뭐예요?"}).json()["thread_id"]
    msgs = c.get(f"/api/children/{cid}/conversations/{tid}", headers=H).json()["messages"]
    assert msgs[0]["content"] == "學이 뭐예요?"
    assert c.get(f"/api/children/{cid}/report/detailed", headers=H).status_code == 402


# ---- AC16 (config only) ------------------------------------------------------------------
def test_llm_config_region_defaults_to_aws_region():
    c, _, svc = make({"LLM_CONFIG": '{"provider": "bedrock"}', "AWS_REGION": "us-east-1"})
    h = c.get("/api/health").json()["llm"]
    assert h["chat_model"] == "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    assert h["question_generation_model"] == "us.anthropic.claude-sonnet-4-6"
    c2, _, _ = make({"LLM_CONFIG": "", "AWS_SESSION_TOKEN": ""})  # empty == unset -> fake LLM
    assert c2.get("/api/health").json()["llm"]["chat"] == "FakeProvider"


def test_every_available_format_generates_for_many_seeds():
    """Regression: 方's 한국어문회 훈 is '모[棱]' (a hanja inside the DB reading); quoting it must not
    make the DB gate reject the exam (it used to surface as an unhandled 500 for some seeds)."""
    import random

    from mockexam.questions import generate_questions
    from tracing.strokes import gradable_chars

    _, _, svc = make()
    exam = svc["exam"]
    wchars = gradable_chars(exam.stroke_db)
    for (body, grade), fmt in exam.formats.items():
        pool = exam.pool_for(body, grade)
        for seed in range(15):
            qs = generate_questions(fmt, pool, wchars if fmt.has_writing else [], random.Random(seed))
            assert len(qs) == fmt.question_count
