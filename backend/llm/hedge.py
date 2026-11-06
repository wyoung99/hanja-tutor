"""Hedged requests: cut the tail latency of a slow first response.

If the first chunk of a call (``stream``) or its whole reply (``complete``) has not arrived within
``hedge_after_s`` seconds, ONE duplicate request is started and whichever produces the first chunk first is
used; the other is cancelled (its stream is closed as soon as it yields) and its output ignored.
``hedge_after_s <= 0`` disables hedging. Provider-agnostic: wraps any ``LLMProvider``.

Error semantics: hedging is for slowness, not for retrying failures. An error before the hedge fired is
raised as-is; once two attempts are in flight, an error from one waits for the other, and only if both fail
is the first error raised. After a winner has been chosen its later errors propagate unchanged.

Every hedged call costs one extra provider request, so ``HedgeStats`` counts calls / hedges / hedge wins.
"""
from __future__ import annotations

import logging
import os
import queue
import threading
import time
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional

from . import LLMProvider

log = logging.getLogger("llm.hedge")

DEFAULT_HEDGE_AFTER_S = 1.5
HEDGE_ENV = "LLM_HEDGE_AFTER_S"
_END = object()


def hedge_after_seconds(env: Optional[Mapping[str, str]] = None, default: float = DEFAULT_HEDGE_AFTER_S) -> float:
    """LLM_HEDGE_AFTER_S from env (default 1.5s); 0 or negative disables; unparsable -> default."""
    env = os.environ if env is None else env
    raw = env.get(HEDGE_ENV)
    if raw in (None, ""):
        return default
    try:
        v = float(raw)
    except ValueError:
        log.warning("ignoring invalid %s=%r; using %ss", HEDGE_ENV, raw, default)
        return default
    return max(0.0, v)


class HedgeStats:
    """Thread-safe counters. calls: wrapped calls; hedged: duplicate requests started (= extra provider
    calls); hedge_won: duplicates that produced the first chunk first."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls = self.hedged = self.hedge_won = 0

    def _add(self, **kw: int) -> None:
        with self._lock:
            for k, v in kw.items():
                setattr(self, k, getattr(self, k) + v)

    def snapshot(self) -> Dict[str, int]:
        with self._lock:
            return {"calls": self.calls, "hedged": self.hedged, "hedge_won": self.hedge_won}


class HedgedProvider(LLMProvider):
    """Wraps a provider; ``complete`` and ``stream`` are hedged. Other attributes (model_id, calls, ...)
    are delegated to the wrapped provider."""

    def __init__(self, inner: LLMProvider, hedge_after_s: float = DEFAULT_HEDGE_AFTER_S,
                 stats: Optional[HedgeStats] = None, name: str = "",
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self.inner = inner
        self.hedge_after_s = max(0.0, float(hedge_after_s or 0))
        self.stats = stats or HedgeStats()
        self.name = name or type(inner).__name__
        self._monotonic = monotonic

    def __getattr__(self, item: str) -> Any:  # only called for attributes not found on self
        if item == "inner":
            raise AttributeError(item)
        return getattr(self.inner, item)

    def chat_model(self) -> Any:
        return self.inner.chat_model()

    # ---- public API --------------------------------------------------------
    def complete(self, system: str, messages: List[Dict[str, str]]) -> str:
        if self.hedge_after_s <= 0:
            self.stats._add(calls=1)
            return self.inner.complete(system, messages)
        race = self._race(lambda: iter([self.inner.complete(system, messages)]))
        try:
            return next(race, "")
        finally:
            race.close()  # stops the loser (its late reply is ignored)

    def stream(self, system: str, messages: List[Dict[str, str]]) -> Iterator[str]:
        if self.hedge_after_s <= 0:
            self.stats._add(calls=1)
            yield from self.inner.stream(system, messages)
            return
        yield from self._race(lambda: self.inner.stream(system, messages))

    # ---- the race ------------------------------------------------------------
    def _race(self, start: Callable[[], Iterator[str]]) -> Iterator[str]:
        """Yield the chunks of the attempt whose first chunk arrives first (at most one hedge)."""
        self.stats._add(calls=1)
        q: "queue.Queue" = queue.Queue()
        stops: List[threading.Event] = []

        def launch() -> int:
            k = len(stops)
            stop = threading.Event()
            stops.append(stop)

            def run() -> None:
                gen = None
                try:
                    gen = start()
                    for chunk in gen:
                        if stop.is_set():
                            break
                        q.put((k, chunk))
                    else:
                        q.put((k, _END))
                except BaseException as e:  # noqa: BLE001 - handed to the consumer
                    q.put((k, e))
                finally:
                    close = getattr(gen, "close", None)
                    if close is not None:
                        try:
                            close()  # cancels the loser's / an abandoned stream
                        except Exception:  # noqa: BLE001
                            pass

            threading.Thread(target=run, daemon=True, name=f"llm-hedge-{self.name}-{k}").start()
            return k

        launch()
        hedge_at = self._monotonic() + self.hedge_after_s
        winner: Optional[int] = None
        failed: Dict[int, BaseException] = {}
        try:
            while winner is None:
                if len(stops) == 1:
                    wait = hedge_at - self._monotonic()
                    if wait <= 0:
                        launch()
                        self.stats._add(hedged=1)
                        log.info("llm hedge fired (%s) after %.2fs without a first chunk", self.name,
                                 self.hedge_after_s)
                        continue
                    try:
                        k, item = q.get(timeout=wait)
                    except queue.Empty:
                        continue
                else:
                    k, item = q.get()
                if isinstance(item, BaseException):
                    failed[k] = item
                    if len(failed) == len(stops):  # nothing left in flight (no hedge yet, or both failed)
                        raise failed[min(failed)]
                    continue
                if k in failed:
                    continue
                winner = k
                for j, s in enumerate(stops):
                    if j != k:
                        s.set()  # cancel the loser
                if k > 0:
                    self.stats._add(hedge_won=1)
                if item is _END:
                    return
                yield item
            while True:
                k, item = q.get()
                if k != winner:
                    continue
                if item is _END:
                    return
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            for s in stops:
                s.set()
