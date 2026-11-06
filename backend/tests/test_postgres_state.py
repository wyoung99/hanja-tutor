"""Accounts and learning state persisted in PostgreSQL (embedded server, a fresh database per test).

* restart survival: two create_app() on the same database -> everything the first one acknowledged is there;
* AC13 atomic usage counting on PostgreSQL (two "servers" racing on one database);
* AC3 deletion: 0 rows carrying the child's id in every table (incl. checkpoints, usage_counters) and
  payment records moved to the separate payment_archive table.
"""
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from fastapi.testclient import TestClient

import auth_helpers as A
from app.main import Clock, create_app
from db.state import TABLES
from notifications import FakePushSender, generate_vapid_keys
from usage import CHATBOT, LimitReached, PostgresCounterStore, UsageLimitConfig, UsageService

pytestmark = pytest.mark.postgres
KST = timezone(timedelta(hours=9))
T0 = datetime(2026, 11, 5, 10, 0, tzinfo=KST)  # Thursday
P256DH = generate_vapid_keys()["public_key"]  # any valid uncompressed P-256 point (a browser's key)
AUTH = "tBHItJI5svbpez7KI4CCXg"


class App:
    def __init__(self, db, clock=None, sender=None):
        self.clock = clock or Clock(T0)
        self.sender = sender or FakePushSender()
        self.app = create_app(clock=self.clock, env={"DATABASE_URL": db, "ADMIN_TOKEN": "adm"},
                              push_sender=self.sender)
        self.c = TestClient(self.app)
        self.svc = self.app.state.services

    def close(self):
        self.app.state.close()


@pytest.fixture
def apps(pg_db):
    made = []

    def make(**kw):
        a = App(pg_db, **kw)
        made.append(a)
        return a

    yield make
    for a in made:
        a.close()
    conn = _CONNS.pop(pg_db, None)
    if conn is not None:
        conn.close()


def signup(c, uid="mom-pg"):
    """Parent id/password sign-up + login (the session is also the client's default header)."""
    return A.parent(c, uid)


def kid(c, H, nick="별이", login_id=None):
    cid = A.new_child(c, H, nick, login_id=login_id)
    assert c.put(f"/api/children/{cid}/target", headers=H, json={"body": "korea_eomunhoe", "grade": "8"}).status_code == 200
    return cid


_CONNS = {}


def count(db, table, where="TRUE", args=()):
    conn = _CONNS.get(db)
    if conn is None or conn.closed:
        conn = _CONNS[db] = psycopg.connect(db, autocommit=True)
    return conn.execute(f"SELECT count(*) FROM {table} WHERE {where}", args).fetchone()[0]


def test_restart_keeps_accounts_and_learning_state(apps, pg_db):
    a = apps()
    c = a.c
    assert c.get("/api/health").json()["state_store"] == "postgres"
    H = signup(c)
    cid = kid(c, H, login_id="star-pg")
    K = A.child_login(c, "star-pg")
    c.headers.update(K)  # learning below runs in the CHILD's session (parent calls pass headers=H)
    for _ in range(2):  # two wrong child passwords: the failure counter must survive the restart too
        assert A.login(c, "star-pg", "wrong").status_code == 401
    # parent edits, learning, tracing, exam in progress, screen time, chat, safety, billing, push
    assert c.put(f"/api/children/{cid}/plan", headers=H, json={"exam_date": "2026-12-20", "daily_count": 5}).status_code == 200
    today = c.get(f"/api/children/{cid}/today").json()
    learned = today["new"][0]["char"]
    assert c.post(f"/api/children/{cid}/today/{learned}/complete").status_code == 200
    stroke_char = next(x["char"] for x in today["new"] if x["tracing"] == "graded")
    assert c.post(f"/api/children/{cid}/tracing/{stroke_char}/start").json()["mode"] == "graded"
    assert c.post(f"/api/children/{cid}/tracing/{stroke_char}/stroke", json={"stroke": [[0, 0], [1, 1]]}).json()["ok"] is False
    ex = c.post(f"/api/children/{cid}/exams", json={"seed": 7}).json()
    q = ex["questions"][0]
    c.post(f"/api/exams/{ex['exam_id']}/answer", json={"question_id": q["id"], "value": q["options"][0]})
    assert c.put(f"/api/children/{cid}/screentime", headers=H, json={"daily_minutes": 60, "windows": []}).status_code == 200
    c.post(f"/api/children/{cid}/heartbeat")
    a.clock.offset = timedelta(minutes=1)
    c.post(f"/api/children/{cid}/heartbeat")
    assert c.post("/api/parents/me/push-subscriptions", headers=H,
                  json={"endpoint": "https://push.example.com/send/abc", "keys": {"p256dh": P256DH, "auth": AUTH}}
                  ).status_code == 201
    req = c.post(f"/api/children/{cid}/extension-requests", json={"minutes": 15}).json()
    chat = c.post(f"/api/children/{cid}/chat", json={"message": "學이 뭐예요?"}).json()
    assert chat["status"] == "answered"
    c.post(f"/api/children/{cid}/chat", json={"message": "내 전화번호는 010-1234-5678이야"})
    sub = c.post("/api/billing/subscribe", headers=H, json={"plan_id": "monthly", "auth_key": "ak"})
    assert sub.status_code == 201, sub.text
    before = {
        "children": c.get("/api/children", headers=H).json(),
        "plan": c.get(f"/api/children/{cid}/plan", headers=H).json(),
        "today": c.get(f"/api/children/{cid}/today").json(),
        "exam": c.get(f"/api/children/{cid}/exams/active").json(),
        "screentime": c.get(f"/api/children/{cid}/screentime", headers=H).json()["daily_minutes"],
        "usage": c.get(f"/api/children/{cid}/usage").json()["chatbot"]["used"],
        "threads": c.get(f"/api/children/{cid}/threads").json(),
        "safety": c.get(f"/api/children/{cid}/safety-events", headers=H).json(),
        "notes": [(n["seq"], n["kind"]) for n in c.get("/api/parents/me/notifications", headers=H).json()],
        "sub": c.get("/api/billing/subscription", headers=H).json(),
        "pending": c.get("/api/parents/me/extension-requests", headers=H).json(),
    }
    assert before["usage"] == 1 and before["safety"] and before["pending"]
    a.close()

    # ---- "restart": a brand-new app on the same database; the parent's session token still works ----
    b = apps(clock=Clock(T0 + timedelta(minutes=2)))
    c2 = b.c
    c2.headers.update(K)  # the child's session token survived the restart as well
    assert c2.get("/api/children", headers=H).json() == before["children"]
    assert c2.get(f"/api/children/{cid}/plan", headers=H).json() == before["plan"]
    assert c2.get(f"/api/children/{cid}/today").json() == before["today"]  # same frozen day plan
    planner = b.svc["planners"][cid]
    assert learned in planner.learned and learned in planner.reviews  # review item survived
    assert c2.get(f"/api/children/{cid}/exams/active").json()["exam_id"] == before["exam"]["exam_id"]
    assert c2.get(f"/api/children/{cid}/exams/active").json()["answers"] == {q["id"]: q["options"][0]}
    assert c2.get(f"/api/children/{cid}/screentime", headers=H).json()["daily_minutes"] == 60
    assert b.svc["screentime"].used_seconds(cid, T0.date()) == 60
    assert c2.get(f"/api/children/{cid}/usage").json()["chatbot"]["used"] == 1
    assert c2.get(f"/api/children/{cid}/threads").json() == before["threads"]
    assert c2.get(f"/api/children/{cid}/safety-events", headers=H).json() == before["safety"]
    assert c2.get("/api/billing/subscription", headers=H).json() == before["sub"]
    assert c2.get("/api/parents/me/extension-requests", headers=H).json() == before["pending"]
    notes = [(n["seq"], n["kind"]) for n in c2.get("/api/parents/me/notifications", headers=H).json()]
    assert notes == before["notes"]
    assert len(c2.get("/api/parents/me/push-subscriptions", headers=H).json()) == 1
    # state keeps evolving after the restart: parent approves the request with a push-capable inbox
    assert c2.post(f"/api/parents/me/extension-requests/{req['id']}", headers=H, json={"approve": True}).status_code == 200
    assert c2.post(f"/api/children/{cid}/screentime/extra",  # parent's account password (hash survived)
                   json={"parent_password": A.PARENT_PW, "minutes": 5}).status_code == 200
    c2.post(f"/api/children/{cid}/chat", json={"message": "校는 뭐예요?"})
    assert c2.get(f"/api/children/{cid}/usage").json()["chatbot"]["used"] == 2
    tid = before["threads"][0]["thread_id"]
    assert len(c2.get(f"/api/children/{cid}/threads/{tid}").json()["messages"]) == 2
    # accounts survive the restart: id/password login for both roles, ids stay taken (login index restored)
    assert A.login(c2, "MOM-PG", A.PARENT_PW).json()["role"] == "parent"
    assert A.login(c2, "mom-pg", "wrong-pass-1").status_code == 401
    r = A.login(c2, "star-pg", "wrong")
    assert r.status_code == 401 and r.json()["detail"]["remaining"] == 2  # 3rd failure of 5 (persisted)
    assert A.login(c2, "star-pg", A.CHILD_PW).json()["role"] == "child"
    again = A.signup(c2, "Star-PG")
    assert again.status_code == 409 and again.json()["detail"]["code"] == "login_id_taken"
    assert count(pg_db, "parents") == 1 and count(pg_db, "children") == 1
    assert count(pg_db, "review_items", "child_id = %s", (cid,)) >= 1


def test_passwords_are_never_stored_in_plaintext(apps, pg_db):
    a = apps()
    c = a.c
    H = signup(c, "plain-check")
    cid = kid(c, H, login_id="plain-kid")
    K = A.child_login(c, "plain-kid")
    extra = f"/api/children/{cid}/screentime/extra"
    c.post(extra, headers=K, json={"parent_password": "typo-pw-1", "minutes": 5})  # a wrong try is stored as a count
    c.post(f"/api/children/{cid}/reset-password", headers=H, json={"password": "newkid99"})
    a.close()
    conn = psycopg.connect(pg_db, autocommit=True)
    try:
        dump = " ".join(str(row) for t in TABLES for row in conn.execute(f"SELECT key, doc::text FROM {t}"))
        docs = [r[0] for r in conn.execute("SELECT doc FROM parents UNION ALL SELECT doc FROM children")]
    finally:
        conn.close()
    for secret in (A.PARENT_PW, A.CHILD_PW, "newkid99", "typo-pw-1"):
        assert secret not in dump, secret
    assert len(docs) == 2
    for d in docs:
        assert d["password_hash"].startswith("scrypt$16384$8$1$") and len(d["password_hash"].split("$")) == 6
        assert not any(k in d for k in ("password", "pin_hash", "pin_salt", "ci_hash"))
    assert count(pg_db, "parent_password_failures") == 1 and count(pg_db, "child_sessions") == 0  # reset signs out
    with psycopg.connect(pg_db, autocommit=True) as conn2:  # the obsolete parent PIN table is gone (schema v2)
        assert conn2.execute("SELECT to_regclass('parent_pins')").fetchone()[0] is None
    conn = _CONNS.pop(pg_db, None)
    if conn is not None:
        conn.close()


def test_usage_counting_is_atomic_across_two_servers_on_one_database(pg_db):
    clock = lambda: T0  # noqa: E731
    stores = [PostgresCounterStore(pg_db), PostgresCounterStore(pg_db)]
    svcs = [UsageService(UsageLimitConfig.from_mapping({CHATBOT: 20}), clock, counters=s) for s in stores]
    ok, refused = [], []
    lock = threading.Lock()
    barrier = threading.Barrier(40)

    def hit(i):
        barrier.wait()
        try:
            r = svcs[i % 2].consume("kid", CHATBOT)
            with lock:
                ok.append(r)
        except LimitReached:
            with lock:
                refused.append(i)

    with ThreadPoolExecutor(40) as pool:
        list(pool.map(hit, range(40)))
    assert len(ok) == 20 and len(refused) == 20
    assert stores[0].get("kid", CHATBOT, ok[0].period) == 20
    svcs[1].release(ok[0])  # failed answer gives the unit back
    assert svcs[0].snapshot("kid", CHATBOT)["used"] == 19
    assert svcs[0].consume("sibling", CHATBOT)  # siblings count separately
    for s in stores:
        s.close()


def test_two_devices_racing_over_http_never_pass_the_limit(apps):
    a = apps()
    H = signup(a.c)
    cid = kid(a.c, H, login_id="race-kid")
    K = A.child_login(a.c, "race-kid")  # both devices are logged in as the child

    def ask(i):
        return TestClient(a.app).post(f"/api/children/{cid}/chat", headers=K,
                                      json={"message": "學이 뭐예요?"}).json()["status"]

    with ThreadPoolExecutor(12) as pool:
        statuses = list(pool.map(ask, range(30)))
    assert statuses.count("answered") == 20 and statuses.count("locked") == 10
    assert a.c.get(f"/api/children/{cid}/usage").json()["chatbot"]["used"] == 20


def test_child_deletion_leaves_zero_rows_and_account_deletion_archives_payments(apps, pg_db):
    a = apps()
    c = a.c
    H = signup(c)
    cid, cid2 = kid(c, H, login_id="del-kid"), kid(c, H, "달이")
    A.child_login(c, "del-kid")  # a child session row (child_sessions) that must go with the child
    for x in (cid, cid2):
        today = c.get(f"/api/children/{x}/today").json()
        c.post(f"/api/children/{x}/today/{today['new'][0]['char']}/complete")
        c.post(f"/api/children/{x}/exams", json={"seed": 3})
        c.post(f"/api/children/{x}/chat/stream", json={"message": "學이 뭐예요?"})
        c.post(f"/api/children/{x}/chat", json={"message": "내 전화번호는 010-1234-5678이야"})
        c.put(f"/api/children/{x}/screentime", headers=H, json={"daily_minutes": 30, "windows": []})
        c.post(f"/api/children/{x}/heartbeat")
        c.post(f"/api/children/{x}/extension-requests", json={"minutes": 10})
    assert c.post("/api/billing/subscribe", headers=H, json={"plan_id": "monthly", "auth_key": "ak"}).status_code == 201
    c.get(f"/api/children/{cid}/report/detailed", headers=H)  # paid-use event row for the child
    tables_with_child = [t for t in TABLES if count(pg_db, t, "child_id = %s", (cid,))]
    assert {"children", "child_sessions", "study_plans", "review_items", "mock_exams", "safety_events", "screentime_days",
            "extension_requests", "notifications", "paid_usage_events"} <= set(tables_with_child)
    assert count(pg_db, "usage_counters", "child_id = %s", (cid,)) >= 1
    tids = [t["thread_id"] for t in c.get(f"/api/children/{cid}/threads").json()]
    assert tids and count(pg_db, "checkpoints", "thread_id = ANY(%s)", (tids,)) >= 1

    r = c.delete(f"/api/children/{cid}", headers=H).json()
    assert r["remaining_learning_records"] == 0
    for t in TABLES + ("usage_counters",):
        assert count(pg_db, t, "child_id = %s", (cid,)) == 0, t
    for t in ("checkpoints", "checkpoint_blobs", "checkpoint_writes", "chat_threads"):
        assert count(pg_db, t, "thread_id = ANY(%s)", (tids,)) == 0, t
    assert count(pg_db, "children", "child_id = %s", (cid2,)) == 1  # the sibling is untouched

    pid = a.svc["acc"].parent_for_session(H["Authorization"][7:]).id
    assert count(pg_db, "payments", "parent_id = %s", (pid,)) == 1
    r = c.delete("/api/parents/me", headers=H).json()
    assert r["remaining_learning_records"] == 0 and r["archived_payments"] == 1
    assert count(pg_db, "payments", "parent_id = %s", (pid,)) == 0
    assert count(pg_db, "payment_archive", "parent_id = %s", (pid,)) == 1  # separate 5-year retention table
    for t in TABLES + ("usage_counters",):
        if t != "payment_archive":
            assert count(pg_db, t, "child_id = %s OR parent_id = %s" if t != "usage_counters" else "child_id = %s",
                         (cid2, pid) if t != "usage_counters" else (cid2,)) == 0, t
    a.close()
    b = apps()  # after a restart the account is still gone, the archive is still there
    assert b.svc["acc"].parents == {} and len(b.svc["life"].archive.rows) == 1
