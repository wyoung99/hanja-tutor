"""Streaming chatbot answers (SSE-ready events).

Events (dicts):
  {"type": "token", "text": ...}    DB-corrected sentence chunk; first one is the "first token"
  {"type": "done", "status", "reply", "sources", ...}   final text (client replaces streamed text)
  {"type": "error", "message", "retry": True}            failure/timeout -> notice + retry button

Guarantees: a failed (error / timeout / client-abort / empty) response releases its usage unit;
the whole answer (safety classification included) must finish within `timeout` seconds (default 15).

Concurrent safety gate (first-token latency): the local checks (consent, safety lock, rule-based
harm/PII/profanity) run first and synchronously. A message they pass is then classified by the LLM
classifier WHILE the answer is already being generated. While the classifier runs, a short Hangul word
question ("학교") is resolved to its hanja via the 우리말샘 원어 (chatbot.wordquery, cached in-process); a resolved
word is on-topic by rule (the classifier can still block it as PII/HARMFUL, but not redirect it) and its hanja
ground the answer prompt. Answer chunks are only buffered until the
classifier allows the message; nothing of the answer is emitted before that. If the classifier blocks or
redirects, the answer stream is cancelled and the result is exactly the sequential one (safety event,
parent notice, 3-strike lock, raw text not stored, no usage charged). Usage is still consumed only after
"allow". Speculation is skipped when the child's daily usage is already exhausted (the answer could never
be shown), so a locked child costs no answer call.
"""
from __future__ import annotations

import json
import queue
import threading
import time
from typing import Callable, Iterator, List, Optional

from usage import FeatureLocked, child_notice

from .service import (FAIL_REPLY, LOCK_REPLY, MAX_SENTENCES, _SENT_SPLIT, ChatResult, postprocess, word_on_topic)

FIRST_TOKEN_SLO_SECONDS = 3.0
STREAM_TIMEOUT_SECONDS = 15.0
_END = object()
_TIMEOUT = object()


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


class _Classification:
    """The LLM classifier call running in a background thread."""

    def __init__(self, classifier, text: str) -> None:
        self._done = threading.Event()
        self._label: Optional[str] = None
        self._exc: Optional[BaseException] = None

        def run() -> None:
            try:
                self._label = classifier.classify(text)
            except BaseException as e:  # noqa: BLE001 - re-raised in the request thread
                self._exc = e
            finally:
                self._done.set()

        threading.Thread(target=run, daemon=True, name="chat-classifier").start()

    def wait(self, timeout: float):
        """The label, or _TIMEOUT if it did not arrive in time."""
        if not self._done.wait(max(0.0, timeout)):
            return _TIMEOUT
        if self._exc is not None:
            raise self._exc
        return self._label


class _AnswerStream:
    """The LLM answer stream consumed by a worker thread into a queue (chunks / _END / exception)."""

    def __init__(self, llm, prep: dict) -> None:
        self.q: "queue.Queue" = queue.Queue()
        self.stop = threading.Event()
        threading.Thread(target=self._run, args=(llm, prep), daemon=True, name="chat-answer").start()

    def _run(self, llm, prep: dict) -> None:
        gen = None
        try:
            gen = llm.stream(prep["system"], prep["msgs"])
            for chunk in gen:
                if self.stop.is_set():
                    return
                self.q.put(chunk)
            self.q.put(_END)
        except BaseException as e:  # noqa: BLE001
            self.q.put(e)
        finally:
            if gen is not None and hasattr(gen, "close"):
                try:
                    gen.close()  # cancel the provider stream (and a pending hedge)
                except Exception:  # noqa: BLE001
                    pass

    def cancel(self) -> None:
        self.stop.set()


def _gated(bot, child_id: str, thread_id: str, gate) -> ChatResult:
    """The non-allow outcome, identical to the sequential path."""
    if gate.action == "block":
        return bot._handle_block(child_id, thread_id, gate)
    return ChatResult("redirected", bot._redirect_reply(child_id), "off_topic")


def stream_ask(bot, child_id: str, thread_id: str, message: str, history, timeout: float = STREAM_TIMEOUT_SECONDS,
               monotonic: Callable[[], float] = time.monotonic, recorder: LatencyRecorder = RECORDER) -> Iterator[dict]:
    from .safety import gate_from_label, rule_gate

    started = monotonic()
    deadline = started + timeout
    bot.accounts.require_chatbot(child_id)
    if bot.is_locked(child_id):
        yield _done(ChatResult("locked", LOCK_REPLY, locked_today=True))
        return
    text = message or ""
    gate = rule_gate(text)  # local + synchronous: rule-based harm / PII / profanity
    pending: Optional[_Classification] = None
    word = None
    if gate is None:
        if bot.classifier is not None:  # start the classifier first: the 우리말샘 word lookup overlaps it
            pending = _Classification(bot.classifier, text)
        try:
            word = bot.resolve_word(text)  # short Hangul word -> hanja (cached); None-safe on failure
        except Exception:  # noqa: BLE001 - a dictionary failure must not break the chat
            word = None
        if pending is None:
            gate = gate_from_label(text, None, word_on_topic(word))
    if gate is not None and gate.action != "allow":
        yield _done(_gated(bot, child_id, thread_id, gate))
        return

    prep = None
    answer: Optional[_AnswerStream] = None
    if pending is not None:  # the LLM classifier decides; overlap it with answer generation
        if bot.usage is None or not bot.usage.is_locked(child_id, "chatbot"):
            try:
                prep = bot._prepare(child_id, message, history or [], word)
            except Exception:  # noqa: BLE001 - redone after the gate so a block/redirect still wins
                prep = None
            if prep is not None and not isinstance(prep, ChatResult):
                answer = _AnswerStream(bot.llm, prep)  # buffered; nothing is emitted before "allow"
    try:
        if pending is not None:
            label = pending.wait(deadline - monotonic())
            if label is _TIMEOUT:  # no verdict within the budget: show nothing of the answer
                yield _error()
                return
            gate = gate_from_label(text, label, word_on_topic(word))
            if gate.action != "allow":
                if answer is not None:
                    answer.cancel()
                yield _done(_gated(bot, child_id, thread_id, gate))
                return

        res = None
        if bot.usage is not None:
            try:
                res = bot.usage.consume(child_id, "chatbot")  # atomic; only after the gate allowed
            except FeatureLocked:
                yield _done(ChatResult("locked", child_notice("chatbot"), locked_today=True))
                return

        ok = False
        try:
            if prep is None:
                prep = bot._prepare(child_id, message, history or [], word)
            if isinstance(prep, ChatResult):  # answered from DB/우리말샘 without the LLM
                ok = prep.counts_as_answer
                if prep.reply:
                    recorder.record(monotonic() - started)
                    yield {"type": "token", "text": prep.reply}
                yield _done(prep)
                return

            if answer is None:
                answer = _AnswerStream(bot.llm, prep)
            q = answer.q
            raw, buf, sent_n, first = "", "", 0, True
            failed = False
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
                    out, bad, _ = postprocess(sent, bot.kb, prep["target"], bot.dictionary, shape=False)
                    if bad:  # unknown hanja: stop streaming; the final event carries the real answer
                        answer.cancel()
                        result = bot._finish(prep, raw)
                        ok = result.counts_as_answer
                        yield _done(result)
                        return
                    if first:
                        recorder.record(monotonic() - started)
                        first = False
                    sent_n += 1
                    yield {"type": "token", "text": out + " "}
            answer.cancel()
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
    finally:
        if answer is not None:
            answer.cancel()  # client abort / block / any exit: stop consuming the answer stream
