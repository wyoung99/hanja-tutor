import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from accounts.service import ChatbotDisabled, Forbidden, NotFound  # noqa: E402
from chatbot.service import ChatbotService  # noqa: E402
from conversations import ConversationService, InMemoryCheckpointer  # noqa: E402
from test_chatbot import setup  # noqa: E402

KST = timezone(timedelta(hours=9))


def make(handler=None, **kw):
    svc, acc, p, child, notifier, clock = setup(handler=handler or (lambda s, m: "學校는 배우는 곳이에요. 學은 배울 학이에요. 校는 학교 교예요."))
    cp = InMemoryCheckpointer()
    conv = ConversationService(svc, acc, cp, **kw)
    return conv, svc, acc, p, child, clock, cp


def test_resume_thread_reflects_prior_context_from_other_device():
    seen = []
    conv, svc, acc, p, child, clock, cp = make(handler=lambda s, m: (seen.append(m), "學校는 배우는 곳이에요. 學은 배울 학이에요. 校는 학교 교예요.")[1])
    tid, r1 = conv.send(child.id, "學校가 뭐야?")
    assert r1.status == "answered"
    # a "new device": a brand-new service object over the same stores
    conv2 = ConversationService(svc, acc, cp, conv.meta)
    assert [t.thread_id for t in conv2.list_threads(child.id)] == [tid]
    assert conv2.list_threads(child.id)[0].title == "學校가 뭐야?"
    conv2.send(child.id, "校는?", thread_id=tid)
    last_msgs = seen[-1]
    assert [m["content"] for m in last_msgs][0] == "學校가 뭐야?"  # earlier turn is passed to the LLM
    assert len(last_msgs) == 3
    assert len(conv2.get_messages(child.id, tid)) == 4


def test_blocked_input_not_persisted():
    conv, svc, acc, p, child, clock, cp = make()
    tid, r = conv.send(child.id, "내 전화번호는 010-1234-5678이야")
    assert r.status == "blocked"
    assert conv.get_messages(child.id, tid) == []
    assert "010" not in repr(cp._data) and "010" not in repr(conv.meta.rows)


def test_cap_100_evicts_oldest_including_checkpoint():
    conv, svc, acc, p, child, clock, cp = make()
    t0 = datetime(2026, 11, 5, 9, 0, tzinfo=KST)
    ids = []
    for i in range(100):
        clock.t = t0 + timedelta(minutes=i)
        tid, _ = conv.send(child.id, "學校")
        ids.append(tid)
    assert len(conv.list_threads(child.id)) == 100
    # make thread #5 the most recently used -> thread #0 stays the oldest
    clock.t = t0 + timedelta(minutes=500)
    conv.send(child.id, "學", thread_id=ids[5])
    clock.t = t0 + timedelta(minutes=501)
    new = conv.create_thread(child.id)
    listed = {t.thread_id for t in conv.list_threads(child.id)}
    assert len(listed) == 100 and new.thread_id in listed
    assert ids[0] not in listed and ids[5] in listed and ids[1] in listed
    assert ids[0] not in cp.thread_ids() and len(cp.thread_ids()) == 99  # new thread has no checkpoint yet
    with pytest.raises(NotFound):
        conv.get_messages(child.id, ids[0])


def test_cap_is_per_child():
    conv, svc, acc, p, child, clock, cp = make(max_threads=2)
    other = acc.create_child(p.id, "달이", "1234", ai_consent=True)
    a = conv.create_thread(child.id)
    conv.create_thread(other.id)
    conv.create_thread(other.id)
    conv.create_thread(child.id)
    assert a.thread_id in {t.thread_id for t in conv.list_threads(child.id)}
    assert len(conv.list_threads(other.id)) == 2


def _residue(conv, cp, tid):
    return (
        tid in cp.thread_ids(),
        tid in conv.meta.rows,
        tid in conv.chatbot._thread_counts,
        tid in repr(conv.meta.rows) or tid in repr(cp._data),
    )


def test_child_delete_is_permanent_everywhere():
    conv, svc, acc, p, child, clock, cp = make()
    tid, _ = conv.send(child.id, "學校가 뭐야?")
    conv.delete_thread_by_child(child.id, tid)
    assert _residue(conv, cp, tid) == (False, False, False, False)
    assert tid not in [t.thread_id for t in conv.list_threads(child.id)]
    assert tid not in [t.thread_id for t in conv.parent_list_threads(p.id, child.id)]
    with pytest.raises(NotFound):
        conv.parent_get_messages(p.id, child.id, tid)
    with pytest.raises(NotFound):
        conv.send(child.id, "이어서", thread_id=tid)
    assert cp.load(tid) == []


def test_parent_delete_and_view_and_authorization():
    conv, svc, acc, p, child, clock, cp = make()
    tid, _ = conv.send(child.id, "學校가 뭐야?")
    assert len(conv.parent_get_messages(p.id, child.id, tid)) == 2
    with pytest.raises(Forbidden):
        conv.parent_list_threads("stranger", child.id)
    with pytest.raises(Forbidden):
        conv.delete_thread_by_parent("stranger", child.id, tid)
    other = acc.create_child(p.id, "달이", "1234", ai_consent=True)
    with pytest.raises(Forbidden):
        conv.get_messages(other.id, tid)
    conv.delete_thread_by_parent(p.id, child.id, tid)
    assert _residue(conv, cp, tid) == (False, False, False, False)
    assert conv.parent_list_threads(p.id, child.id) == []


def test_safety_count_does_not_leak_after_delete_and_no_consent_disabled():
    conv, svc, acc, p, child, clock, cp = make()
    tid, _ = conv.send(child.id, "씨발")
    assert svc._thread_counts.get(tid) == 1
    conv.delete_thread_by_child(child.id, tid)
    assert tid not in svc._thread_counts
    nc = acc.create_child(p.id, "노동의", "1234", ai_consent=False)
    with pytest.raises(ChatbotDisabled):
        conv.create_thread(nc.id)
