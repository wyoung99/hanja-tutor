"""LLM provider interface. Swap providers by configuration only.

Config example: {"provider": "bedrock", "region": "ap-northeast-2"}
  -> Haiku 4.5 for everyday tasks, Sonnet 4.6 only for reasoning-heavy tasks (see TASK_TIERS).
Legacy single-model config {"provider": "bedrock", "model_id": "...", "region": "..."} still works.
"""
from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, Iterator, List


class LLMError(Exception):
    pass


class LLMProvider(ABC):
    """Adapter the chatbot depends on; nothing else imports a vendor SDK."""

    @abstractmethod
    def complete(self, system: str, messages: List[Dict[str, str]]) -> str:
        """messages: [{"role": "user"|"assistant", "content": str}] -> reply text."""

    def chat_model(self) -> Any:
        """LangChain chat model for create_agent / LangGraph wiring."""
        raise NotImplementedError

    def stream(self, system: str, messages: List[Dict[str, str]]) -> Iterator[str]:
        """Yield reply text chunks. Default: one chunk from complete() (providers may override)."""
        yield self.complete(system, messages)


class BedrockProvider(LLMProvider):
    """Default provider: langchain_aws.ChatBedrockConverse (imported lazily)."""

    def __init__(self, model_id: str, region: str | None = None, temperature: float = 0.2, **kwargs: Any):
        self.model_id, self.region, self.temperature, self.kwargs = model_id, region, temperature, kwargs
        self._model: Any = None

    def chat_model(self) -> Any:
        if self._model is None:
            from langchain_aws import ChatBedrockConverse

            args = dict(model=self.model_id, temperature=self.temperature, **self.kwargs)
            if self.region:
                args["region_name"] = self.region
            self._model = ChatBedrockConverse(**args)
        return self._model

    def complete(self, system: str, messages: List[Dict[str, str]]) -> str:
        try:
            msgs = [("system", system)] + [(m["role"], m["content"]) for m in messages]
            out = self.chat_model().invoke(msgs)
            content = out.content
            if isinstance(content, list):
                content = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
            return str(content)
        except Exception as e:  # noqa: BLE001 - surfaced as a failed response
            raise LLMError(str(e)) from e


def _chunk_text(content: Any) -> str:
    if isinstance(content, list):
        return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
    return str(content or "")


def _bedrock_stream(self: "BedrockProvider", system: str, messages: List[Dict[str, str]]) -> Iterator[str]:
    try:
        msgs = [("system", system)] + [(m["role"], m["content"]) for m in messages]
        for chunk in self.chat_model().stream(msgs):
            t = _chunk_text(chunk.content)
            if t:
                yield t
    except Exception as e:  # noqa: BLE001
        raise LLMError(str(e)) from e


BedrockProvider.stream = _bedrock_stream  # type: ignore[method-assign]


class FakeProvider(LLMProvider):
    """Deterministic provider for tests/dev: handler(system, messages) -> text.

    Latency injection (for hedging / concurrency tests): ``first_delay`` is the delay before the first chunk
    (stream) or before returning (complete). It is a float, a list indexed by call number (0-based; the last
    value repeats) or a callable(call_index, system) -> float. ``chunk_delay`` sleeps between later chunks.
    ``cancelled`` lists the call indexes whose stream was closed before it finished.
    """

    def __init__(self, handler: Callable[[str, List[Dict[str, str]]], str] | None = None,
                 first_delay: Any = 0.0, chunk_delay: float = 0.0):
        self.handler = handler or (lambda s, m: "")
        self.calls: List[tuple] = []
        self.first_delay, self.chunk_delay = first_delay, chunk_delay
        self.cancelled: List[int] = []
        self._n = 0
        self._lock = threading.Lock()

    def _begin(self, system: str, messages: List[Dict[str, str]]) -> tuple:
        with self._lock:
            idx = self._n
            self._n += 1
            self.calls.append((system, messages))
        d = self.first_delay
        if callable(d):
            d = d(idx, system)
        elif isinstance(d, (list, tuple)):
            d = d[min(idx, len(d) - 1)] if d else 0.0
        return idx, float(d or 0.0)

    def complete(self, system: str, messages: List[Dict[str, str]]) -> str:
        idx, delay = self._begin(system, messages)
        if delay:
            time.sleep(delay)
        return self.handler(system, messages)

    def stream(self, system: str, messages: List[Dict[str, str]]) -> Iterator[str]:
        """Streams the handler's reply in small pieces; handler may also return a list/iterator of chunks."""
        idx, delay = self._begin(system, messages)
        try:
            if delay:
                time.sleep(delay)
            out = self.handler(system, messages)
            pieces = (out[i:i + 8] for i in range(0, len(out), 8)) if isinstance(out, str) else out
            for n, piece in enumerate(pieces):
                if n and self.chunk_delay:
                    time.sleep(self.chunk_delay)
                yield piece
        except GeneratorExit:  # consumer closed the stream early (cancelled)
            with self._lock:
                self.cancelled.append(idx)
            raise


_REGISTRY: Dict[str, Callable[..., LLMProvider]] = {"bedrock": BedrockProvider, "fake": FakeProvider}


def register_provider(name: str, factory: Callable[..., LLMProvider]) -> None:
    _REGISTRY[name] = factory


def build_provider(config: Dict[str, Any]) -> LLMProvider:
    cfg = dict(config)
    name = cfg.pop("provider", "bedrock")
    if name not in _REGISTRY:
        raise LLMError(f"unknown LLM provider: {name}")
    return _REGISTRY[name](**cfg)


# ---- model tiers -------------------------------------------------------------
# Default is the fast/cheap tier (Haiku). Only tasks that need multi-step reasoning or
# must produce carefully constructed content go to the reasoning tier (Sonnet).
FAST = "fast"            # Claude Haiku 4.5
REASONING = "reasoning"  # Claude Sonnet 4.6

TASK_TIERS: Dict[str, str] = {
    "chat": FAST,                       # literacy chatbot answer: 3~5 short sentences, DB-corrected in code
    "safety_classifier": FAST,          # one-word label (HARMFUL/PII/ON_TOPIC/OFF_TOPIC)
    "question_generation": REASONING,   # mock-exam items: distractors + explanations as strict JSON
}

# First-chunk latency of these tasks is user-visible (chatbot first token), so they are hedged
# (see llm.hedge). Sonnet question generation is never hedged.
HEDGED_TASKS = frozenset({"chat", "safety_classifier"})

# Bedrock inference-profile IDs per region. ap-northeast-2 offers only `global.*` profiles;
# us-* regions use the `us.*` profiles so requests stay inside US regions.
BEDROCK_MODEL_IDS: Dict[str, Dict[str, str]] = {
    "us-east-1": {
        FAST: "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        REASONING: "us.anthropic.claude-sonnet-4-6",
    },
    "us-west-2": {
        FAST: "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        REASONING: "us.anthropic.claude-sonnet-4-6",
    },
    "ap-northeast-2": {
        FAST: "global.anthropic.claude-haiku-4-5-20251001-v1:0",
        REASONING: "global.anthropic.claude-sonnet-4-6",
    },
}
DEFAULT_BEDROCK_REGION = "ap-northeast-2"


def bedrock_model_ids(region: str) -> Dict[str, str]:
    if region not in BEDROCK_MODEL_IDS:
        raise LLMError(f"no Bedrock model table for region {region!r}; set explicit 'tiers' in LLM_CONFIG")
    return dict(BEDROCK_MODEL_IDS[region])


class LLMRouter:
    """Picks the provider for a task by tier. Unknown tasks fall back to the fast tier."""

    def __init__(self, providers: Dict[str, LLMProvider], task_tiers: Dict[str, str] | None = None):
        if FAST not in providers:
            raise LLMError("LLM router needs a 'fast' tier provider")
        self.providers = dict(providers)
        self.task_tiers = dict(TASK_TIERS if task_tiers is None else task_tiers)
        for task, tier in self.task_tiers.items():
            if tier not in self.providers:
                raise LLMError(f"task {task!r} mapped to unconfigured tier {tier!r}")

    def tier_of(self, task: str) -> str:
        return self.task_tiers.get(task, FAST)

    def for_task(self, task: str) -> LLMProvider:
        return self.providers[self.tier_of(task)]

    def hedged(self, task: str, hedge_after_s: float) -> LLMProvider:
        """for_task() wrapped in a HedgedProvider for latency-critical tasks (HEDGED_TASKS); other tasks
        (e.g. Sonnet question generation) get the plain provider. One wrapper (and stats) per task."""
        if task not in HEDGED_TASKS:
            return self.for_task(task)
        cache = self.__dict__.setdefault("_hedged", {})
        if task not in cache:
            cache[task] = HedgedProvider(self.for_task(task), hedge_after_s, name=task)
        return cache[task]

    def hedge_stats(self) -> Dict[str, Any]:
        """{task: {"hedge_after_s", "calls", "hedged", "hedge_won"}} for the hedged wrappers handed out."""
        cache = self.__dict__.get("_hedged", {})
        return {t: {"hedge_after_s": p.hedge_after_s, **p.stats.snapshot()} for t, p in cache.items()}


def build_llm_router(config: Dict[str, Any]) -> LLMRouter:
    """Build a tiered router from LLM_CONFIG.

    Accepted shapes:
      {"provider": "bedrock", "region": "ap-northeast-2"}          -> Haiku/Sonnet IDs from BEDROCK_MODEL_IDS
      {"tiers": {"fast": {...provider cfg}, "reasoning": {...}}}     -> explicit per-tier provider configs
      {"provider": ..., "model_id": ...}  (legacy single model)     -> that one model for every task
    Optional "task_tiers": {"<task>": "fast"|"reasoning"} overrides TASK_TIERS entries.
    """
    cfg = dict(config)
    task_tiers = {**TASK_TIERS, **cfg.pop("task_tiers", {})}
    tiers_cfg = cfg.pop("tiers", None)
    if tiers_cfg is None:
        if cfg.get("provider", "bedrock") == "bedrock" and "model_id" not in cfg:
            region = cfg.pop("region", DEFAULT_BEDROCK_REGION)
            tiers_cfg = {tier: {**cfg, "provider": "bedrock", "region": region, "model_id": mid}
                         for tier, mid in bedrock_model_ids(region).items()}
        else:
            single = build_provider(cfg)
            return LLMRouter({FAST: single, REASONING: single}, task_tiers)
    providers = {tier: build_provider(tc) for tier, tc in tiers_cfg.items()}
    return LLMRouter(providers, task_tiers)


from .hedge import (DEFAULT_HEDGE_AFTER_S, HEDGE_ENV, HedgedProvider, HedgeStats,  # noqa: E402,F401
                    hedge_after_seconds)
