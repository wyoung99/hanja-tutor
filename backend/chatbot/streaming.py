"""Streaming chatbot answers (SSE-ready events).

Events (dicts):
  {"type": "token", "text": ...}    DB-corrected sentence chunk; first one is the "first token"
  {"type": "done", "status", "reply", "sources", ...}   final text (client replaces streamed text)
  {"type": "error", "message", "retry": True}            failure/timeout -> notice + retry button

Guarantees: a failed (error / timeout / client-abort / empty) response releases its usage unit;
the whole answer must finish within `timeout` seconds (default 15).
"""
from __future__ import annotations

import json
import queue
import threading
import time
from typing import Callable, Dict, Iterator, List, Optional

from llm import LLMError
from usage import FeatureLocked, child_notice

from .service import (FAIL_REPLY, LOCK_REPLY, MAX_SENTENCES, _SENT_SPLIT, ChatResult, postprocess)

FIRST_TOKEN_SLO_SECONDS = 3.0
STREAM_TIMEOUT_SECONDS = 15.0
_END = object()


def sse(event: dict) -> str:
    """Format one event as a Server-Sent-Events frame (for the FastAPI StreamingResponse)."""
    return f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


class LatencyRecorder:
    """Collects first-token latencies; p95 is what staging checks against the 3s target."""

    def __init__(self) -> None:
        self.first_token: List[float] = []
        self._lock = threading.Lock()

    def record(self, seconds: float) -> None:
        with self._lock:
            self.first_token.append(seconds)

    def p95(self) -> Optional[float]:
        with self._lock:
            xs = sorted(self.first_token)
        if not xs:
            return None
        return xs[min(len(xs) - 1, max(0, -(-95 * len(xs) // 100) - 1))]


RECORDER = LatencyRecorder()


def _error() -> dict:
    return {"type": "error", "message": FAIL_REPLY, "retry": True, "status": "error"}


def _done(res: ChatResult) -> dict:
    return {"type": "done", "status": res.status, "reply": res.reply, "category": res.category,
            "locked_today": res.locked_today, "sources": res.sources}


def stream_ask(bot, child_id: str, thread_id: str, message: str, history, timeout: float = STREAM_TIMEOUT_SECONDS,
               monotonic: Callable[[], float] = time.monotonic, recorder: LatencyRecorder = RECORDER) -> Iterator[dict]:
    from .safety import gate_message

    bot.accounts.require_chatbot(child_id)
    if bot.is_locked(child_id):
        yield _done(ChatResult("locked", LOCK_REPLY, locked_today=True))
        return
    gate = gate_message(message or "", bot.classifier)
    if gate.action == "block":
        yield _done(bot._handle_block(child_id, thread_id, gate))
        return
    if gate.action == "redirect":
        yield _done(ChatResult("redirected", bot._redirect_reply(child_id), "off_topic"))
        return

    started = monotonic()
    res = None
    if bot.usage is not None:
        try:
            res = bot.usage.consume(child_id, "chatbot")  # atomic
        except FeatureLocked:
            yield _done(ChatResult("locked", child_notice("chatbot"), locked_today=True))
            return

    ok = False
    try:
        prep = bot._prepare(child_id, message, history or [])
        if isinstance(prep, ChatResult):  # answered from DB/우리말샘 without the LLM
            ok = prep.counts_as_answer
            if prep.reply:
                recorder.record(monotonic() - started)
                yield {"type": "token", "text": prep.reply}
            yield _done(prep)
            return

        q: "queue.Queue" = queue.Queue()
        stop = threading.Event()

        def worker() -> None:
            try:
                for chunk in bot.llm.stream(prep["system"], prep["msgs"]):
                    if stop.is_set():
                        return
                    q.put(chunk)
                q.put(_END)
            except BaseException as e:  # noqa: BLE001
                q.put(e)

        threading.Thread(target=worker, daemon=True).start()
        deadline = started + timeout
        raw, buf, sent_n, first = "", "", 0, True
        failed = False
        try:
            while True:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    failed = True
                    break
                try:
                    item = q.get(timeout=min(remaining, 0.05))
                except queue.Empty:
                    continue
                if item is _END:
                    break
                if isinstance(item, BaseException):
                    failed = True
                    break
                raw += item
                buf += item
                parts = _SENT_SPLIT.split(buf)
                complete, buf = parts[:-1], parts[-1]
                for sent in complete:
                    if sent_n >= MAX_SENTENCES or not sent.strip():
                        continue
                    text, bad, _ = postprocess(sent, bot.kb, prep["target"], bot.dictionary, shape=False)
                    if bad:  # unknown hanja: stop streaming; the final event carries the real answer
                        stop.set()
                        result = bot._finish(prep, raw)
                        ok = result.counts_as_answer
                        yield _done(result)
                        return
                    if first:
                        recorder.record(monotonic() - started)
                        first = False
                    sent_n += 1
                    yield {"type": "token", "text": text + " "}
        finally:
            stop.set()
        if failed or not raw.strip():
            yield _error()
            return
        result = bot._finish(prep, raw)
        ok = result.counts_as_answer
        if result.status == "error":
            yield _error()
            return
        if first:
            recorder.record(monotonic() - started)
        yield _done(result)
    finally:
        if res is not None and not ok:  # error, timeout, client disconnect, non-answer -> free
            bot.usage.release(res)
