"""AC10/AC3 against a real PostgreSQL (embedded pixeltable-pgserver): LangGraph PostgresSaver checkpoints
plus the chat_threads metadata table. Skips (with the reason) only if the embedded server cannot start."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from accounts.service import AccountService
import auth_helpers as A  # noqa: E402
from app.main import Clock, create_app
from chatbot.knowledge import HanjaKnowledge
from chatbot.service import ChatbotService, RecordingNotifier
from conversations import ConversationService, PostgresCheckpointer, PostgresThreadMeta
from hanja_db.store import HanjaStore
from lifecycle import LifecycleService
from llm import FakeProvider

pytestmark = pytest.mark.postgres
KST = timezone(timedelta(hours=9))
CP_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")
KB = HanjaKnowledge.from_store(HanjaStore.load())


class Tick:
    def __init__(self):
        self.t = datetime(2026, 11, 5, 9, 0, tzinfo=KST)

    def __call__(self):
        self.t += timedelta(seconds=1)
        return self.t


def remembering_llm():
    """Echoes the earlier user turns so context carry-over is observable."""
    def h(system, msgs):
        prev = [m["content"] for m in msgs[:-1] if m["role"] == "user"]
        return f"앞에서 {len(prev)}번 물었어요. 學은 배우다예요. 같이 공부해요."
    return FakeProvider(h)


def world(uri, accounts=None, clock=None):
    clock = clock or Tick()
    acc = accounts or AccountService(clock)
    bot = ChatbotService(acc, KB, remembering_llm(), None, RecordingNotifier(), clock, use_llm_classifier=False)
    conv = ConversationService(bot, acc, PostgresCheckpointer.from_conn_string(uri), PostgresThreadMeta(uri))
    return acc, bot, conv


def rows(conv, thread_id):
    conn = conv.meta.conn
    n = {t: conn.execute(f"SELECT count(*) FROM {t} WHERE thread_id = %s", (thread_id,)).fetchone()[0]
         for t in CP_TABLES}
    n["chat_threads"] = conv.meta.count_rows(thread_id)
    return n


def child(acc):
    p = acc.signup_parent("p-" + uuid.uuid4().hex[:12], "parent-pw-1", "a@b.kr", True, True, True)
    c = acc.create_child(p.id, "별이", "1234", ai_consent=True)
    c.extra["target"] = ("korea_eomunhoe", "8")
    return p, c


def test_history_survives_a_new_service_instance_and_context_is_used(pg_uri):
    acc, bot, conv = world(pg_uri)
    p, c = child(acc)
    tid, r = conv.send(c.id, "學이 뭐예요?")
    assert r.status == "answered"
    # "another device / app restart": brand-new service objects on the same database
    _, bot2, conv2 = world(pg_uri, accounts=acc)
    assert [m.thread_id for m in conv2.list_threads(c.id)] == [tid]
    assert [m["role"] for m in conv2.get_messages(c.id, tid)] == ["user", "assistant"]
    _, r2 = conv2.send(c.id, "學校는요?", tid)
    assert "앞에서 1번" in r2.reply  # previous turn was fed back to the model
    assert len(conv2.get_messages(c.id, tid)) == 4
    assert conv2.list_threads(c.id)[0].title == "學이 뭐예요?"


def test_101st_thread_evicts_oldest_with_its_checkpoints(pg_uri):
    acc, bot, conv = world(pg_uri)
    p, c = child(acc)
    first, _ = conv.send(c.id, "學 1")
    for i in range(99):
        conv.send(c.id, f"學 {i + 2}")
    assert len(conv.list_threads(c.id)) == 100
    assert rows(conv, first)["checkpoints"] >= 1
    newest, _ = conv.send(c.id, "學 101")
    ids = {m.thread_id for m in conv.list_threads(c.id)}
    assert len(ids) == 100 and first not in ids and newest in ids
    assert rows(conv, first) == {t: 0 for t in CP_TABLES + ("chat_threads",)}


def test_deleted_thread_leaves_no_rows_and_cannot_be_resumed(pg_uri):
    acc, bot, conv = world(pg_uri)
    p, c = child(acc)
    a, _ = conv.send(c.id, "學이 뭐예요?")
    b, _ = conv.send(c.id, "校는 뭐예요?")
    conv.delete_thread_by_child(c.id, a)
    conv.delete_thread_by_parent(p.id, c.id, b)
    for t in (a, b):
        assert rows(conv, t) == {x: 0 for x in CP_TABLES + ("chat_threads",)}
    assert conv.list_threads(c.id) == [] and conv.parent_list_threads(p.id, c.id) == []
    with pytest.raises(Exception):
        conv.send(c.id, "다시", a)


def test_profile_and_account_deletion_purge_postgres(pg_uri):
    acc, bot, conv = world(pg_uri)
    life = LifecycleService(acc, conv, RecordingNotifier(), clock=bot.clock)
    p, c = child(acc)
    c2 = acc.create_child(p.id, "달이", "1234", ai_consent=True)
    c2.extra["target"] = ("korea_eomunhoe", "8")
    t1, _ = conv.send(c.id, "學이 뭐예요?")
    t2, _ = conv.send(c2.id, "學이 뭐예요?")
    life.delete_child(p.id, c.id)
    assert rows(conv, t1) == {x: 0 for x in CP_TABLES + ("chat_threads",)}
    assert rows(conv, t2)["chat_threads"] == 1
    life.delete_account(p.id)
    assert rows(conv, t2) == {x: 0 for x in CP_TABLES + ("chat_threads",)}


def test_composed_app_on_postgres_resume_and_delete(pg_db):
    clock = Clock(datetime(2026, 11, 5, 10, 0, tzinfo=KST))
    app = create_app(clock=clock, env={"DATABASE_URL": pg_db})
    c = TestClient(app)
    assert c.get("/api/health").json()["conversation_store"] == "postgres"
    H = A.parent(c, "mom-pgconv")
    cid = A.new_child(c, H, "별이", login_id="star-pgconv")
    c.headers.update(A.child_login(c, "star-pgconv"))  # the child chats in its own session
    c.put(f"/api/children/{cid}/target", headers=H, json={"body": "korea_eomunhoe", "grade": "8"})
    tid = c.post(f"/api/children/{cid}/chat/stream", json={"message": "學이 뭐예요?"}).text.split('"thread_id": "')[1][:32]
    assert [m["role"] for m in c.get(f"/api/children/{cid}/threads/{tid}").json()["messages"]] == ["user", "assistant"]
    conv = app.state.services["conv"]
    assert rows(conv, tid)["checkpoints"] >= 1
    r = c.delete(f"/api/children/{cid}", headers=H)
    assert r.json()["remaining_learning_records"] == 0
    assert rows(conv, tid) == {x: 0 for x in CP_TABLES + ("chat_threads",)}
    app.state.close()
