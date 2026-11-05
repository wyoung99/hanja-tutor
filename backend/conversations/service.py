"""Conversation management on top of ChatbotService.

* Message history is persisted through a checkpointer keyed by thread_id
  (production: langgraph-checkpoint-postgres PostgresSaver; tests: in-memory).
* List metadata (child, title, created/last time) lives in a separate store (chat_threads table).
* Each child profile keeps at most 100 threads; creating one more evicts the thread with the
  oldest last-message time, from the checkpointer AND metadata.
* Deleting a thread (by the child or the parent) is permanent.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Protocol

from accounts.service import AccountService, Forbidden, NotFound
from chatbot.service import ChatbotService, ChatResult

MAX_THREADS_PER_CHILD = 100
TITLE_MAX = 30


# ---- checkpointer (message history) ------------------------------------------------
class Checkpointer(Protocol):
    def load(self, thread_id: str) -> List[dict]: ...
    def save(self, thread_id: str, messages: List[dict]) -> None: ...
    def delete_thread(self, thread_id: str) -> None: ...
    def thread_ids(self) -> List[str]: ...


class InMemoryCheckpointer:
    def __init__(self) -> None:
        self._data: Dict[str, List[dict]] = {}

    def load(self, thread_id: str) -> List[dict]:
        return [dict(m) for m in self._data.get(thread_id, [])]

    def save(self, thread_id: str, messages: List[dict]) -> None:
        self._data[thread_id] = [dict(m) for m in messages]

    def delete_thread(self, thread_id: str) -> None:
        self._data.pop(thread_id, None)

    def thread_ids(self) -> List[str]:
        return list(self._data)


class PostgresCheckpointer:
    """Stores the message list as a LangGraph checkpoint via PostgresSaver (thread_id config)."""

    def __init__(self, saver) -> None:  # langgraph.checkpoint.postgres.PostgresSaver (setup() already called)
        self.saver = saver

    @classmethod
    def from_conn_string(cls, conn_string: str):
        from langgraph.checkpoint.postgres import PostgresSaver

        ctx = PostgresSaver.from_conn_string(conn_string)
        saver = ctx.__enter__()
        saver.setup()
        inst = cls(saver)
        inst._ctx = ctx
        return inst

    @staticmethod
    def _cfg(thread_id: str) -> dict:
        return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}

    def load(self, thread_id: str) -> List[dict]:
        tup = self.saver.get_tuple(self._cfg(thread_id))
        if tup is None:
            return []
        return list(tup.checkpoint.get("channel_values", {}).get("messages", []))

    def save(self, thread_id: str, messages: List[dict]) -> None:
        from langgraph.checkpoint.base import empty_checkpoint

        cp = empty_checkpoint()
        cp["channel_values"] = {"messages": list(messages)}
        cp["channel_versions"] = {"messages": 1}
        prev = self.saver.get_tuple(self._cfg(thread_id))
        cfg = prev.config if prev else self._cfg(thread_id)
        self.saver.put(cfg, cp, {"source": "update", "step": len(messages), "writes": {}}, {"messages": 1})

    def delete_thread(self, thread_id: str) -> None:
        self.saver.delete_thread(thread_id)

    def thread_ids(self) -> List[str]:
        return sorted({t.config["configurable"]["thread_id"] for t in self.saver.list(None)})


# ---- metadata store -----------------------------------------------------------------
@dataclass
class ThreadMeta:
    thread_id: str
    child_id: str
    title: str
    created_at: datetime
    last_message_at: datetime


class InMemoryThreadMeta:
    def __init__(self) -> None:
        self.rows: Dict[str, ThreadMeta] = {}


class ConversationService:
    def __init__(
        self,
        chatbot: ChatbotService,
        accounts: AccountService,
        checkpointer: Optional[Checkpointer] = None,
        meta: Optional[InMemoryThreadMeta] = None,
        max_threads: int = MAX_THREADS_PER_CHILD,
    ) -> None:
        self.chatbot, self.accounts = chatbot, accounts
        self.checkpointer = checkpointer or InMemoryCheckpointer()
        self.meta = meta or InMemoryThreadMeta()
        self.max_threads = max_threads

    # ---- helpers -------------------------------------------------------------
    def _child_threads(self, child_id: str) -> List[ThreadMeta]:
        return [m for m in self.meta.rows.values() if m.child_id == child_id]

    def _owned_thread(self, child_id: str, thread_id: str) -> ThreadMeta:
        m = self.meta.rows.get(thread_id)
        if m is None:
            raise NotFound("thread")
        if m.child_id != child_id:
            raise Forbidden("not your conversation")
        return m

    def _purge(self, thread_id: str) -> None:
        self.checkpointer.delete_thread(thread_id)
        self.meta.rows.pop(thread_id, None)
        self.chatbot._thread_counts.pop(thread_id, None)

    def _evict_to_fit(self, child_id: str) -> None:
        threads = self._child_threads(child_id)
        while len(threads) >= self.max_threads:
            oldest = min(threads, key=lambda m: (m.last_message_at, m.created_at))
            self._purge(oldest.thread_id)
            threads = self._child_threads(child_id)

    # ---- child API ---------------------------------------------------------------
    def create_thread(self, child_id: str) -> ThreadMeta:
        self.accounts.require_chatbot(child_id)
        self._evict_to_fit(child_id)
        now = self.chatbot.clock()
        m = ThreadMeta(uuid.uuid4().hex, child_id, "", now, now)
        self.meta.rows[m.thread_id] = m
        return m

    def list_threads(self, child_id: str) -> List[ThreadMeta]:
        if child_id not in self.accounts.children:
            raise NotFound("child")
        return sorted(self._child_threads(child_id), key=lambda m: m.last_message_at, reverse=True)

    def get_messages(self, child_id: str, thread_id: str) -> List[dict]:
        self._owned_thread(child_id, thread_id)
        return self.checkpointer.load(thread_id)

    def send(self, child_id: str, message: str, thread_id: Optional[str] = None) -> tuple:
        """Ask within a thread (new if thread_id is None). Returns (thread_id, ChatResult)."""
        self.accounts.require_chatbot(child_id)
        if thread_id is None:
            thread_id = self.create_thread(child_id).thread_id
        m = self._owned_thread(child_id, thread_id)
        history = self.checkpointer.load(thread_id)
        result: ChatResult = self.chatbot.ask(child_id, thread_id, message, history)
        if result.status in ("answered", "unknown"):  # blocked/redirected/failed input is never stored
            history = history + [
                {"role": "user", "content": message},
                {"role": "assistant", "content": result.reply},
            ]
            self.checkpointer.save(thread_id, history)
            m.last_message_at = self.chatbot.clock()
            if not m.title:
                m.title = message.strip()[:TITLE_MAX]
        return thread_id, result

    def delete_thread_by_child(self, child_id: str, thread_id: str) -> None:
        self._owned_thread(child_id, thread_id)
        self._purge(thread_id)

    # ---- parent API --------------------------------------------------------------
    def _parent_child(self, parent_id: str, child_id: str) -> None:
        child = self.accounts.children.get(child_id)
        if child is None:
            raise NotFound("child")
        if child.parent_id != parent_id:
            raise Forbidden("not your child profile")

    def parent_list_threads(self, parent_id: str, child_id: str) -> List[ThreadMeta]:
        self._parent_child(parent_id, child_id)
        return self.list_threads(child_id)

    def parent_get_messages(self, parent_id: str, child_id: str, thread_id: str) -> List[dict]:
        self._parent_child(parent_id, child_id)
        return self.get_messages(child_id, thread_id)

    def delete_thread_by_parent(self, parent_id: str, child_id: str, thread_id: str) -> None:
        self._parent_child(parent_id, child_id)
        self._owned_thread(child_id, thread_id)
        self._purge(thread_id)
