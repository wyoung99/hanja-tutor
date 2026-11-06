"""Chatbot orchestration: consent -> daily lock -> input guard -> DB-grounded answer."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Protocol

from accounts.service import AccountService, Forbidden, NotFound
from llm import LLMError, LLMProvider

from usage import FeatureLocked, child_notice

from .dictionary import DictionaryClient
from .knowledge import HANJA_CLASS, HANJA_RE, HanjaKnowledge
from .safety import Gate, LLMClassifier, gate_from_label, rule_gate
from .wordquery import lookup_many, resolve_word, syllable_candidates

KST = timezone(timedelta(hours=9))
SAFETY_LOCK_THRESHOLD = 3          # events in one conversation (any type) -> locked for the day
UPPER_MARK = "(상위 급수)"
UNKNOWN_REPLY = "잘 모르겠어요."
LOCK_REPLY = "오늘은 여기까지 할까요? 내일 또 만나요!"
BLOCK_REPLY = "그런 말이나 내용은 여기서 다룰 수 없어요. 한자나 낱말 이야기를 해볼까요?"
FAIL_REPLY = "지금은 답을 만들지 못했어요. 조금 있다가 다시 물어봐 주세요."
FILLER = ["궁금한 한자가 더 있으면 또 물어보세요!", "같이 한 글자씩 익혀 봐요.", "오늘도 한자 공부 멋져요!"]
MIN_SENTENCES, MAX_SENTENCES = 3, 5

_TOKEN_RE = re.compile(
    "(?P<w>[" + HANJA_CLASS + r"]+)(?:\s*[\(\[（](?P<a>[가-힣 ]+?)[\)\]）])?"
)
_SENT_SPLIT = re.compile(r"(?<=[.!?。！？])\s+")


def word_on_topic(word) -> bool:
    """A short Hangul word question resolved to a hanja word counts as on-topic for the gate."""
    return bool(word and word[1] is not None and word[1].is_hanja)


class Notifier(Protocol):
    def notify(self, parent_id: str, kind: str, payload: dict) -> None: ...


class RecordingNotifier:
    def __init__(self) -> None:
        self.sent: List[tuple] = []

    def notify(self, parent_id: str, kind: str, payload: dict) -> None:
        self.sent.append((parent_id, kind, payload))


@dataclass
class SafetyEvent:
    """A blocked input. The raw text is never stored."""
    id: str
    child_id: str
    thread_id: str
    category: str
    source: str
    at: datetime
    thread_count: int
    parent_notified: bool = False


@dataclass
class ChatResult:
    status: str  # answered | redirected | blocked | locked | unknown | error
    reply: str
    category: Optional[str] = None
    locked_today: bool = False
    sources: List[str] = field(default_factory=list)  # "db" / "urimalsaem"

    @property
    def counts_as_answer(self) -> bool:
        """Only real answers are usage-eligible (failures/blocks/redirects/locks are not)."""
        return self.status in ("answered", "unknown")


# ---- pure post-processing -----------------------------------------------------
def postprocess(reply: str, kb: HanjaKnowledge, target: Optional[tuple], dictionary: Optional[DictionaryClient],
                shape: bool = True):
    """Correct 훈·음 to the selected body's DB values, mark above-target hanja, enforce 3~5 sentences.

    Returns (text, unresolved) where unresolved are hanja found neither in the DB nor in 우리말샘.
    """
    unresolved: List[str] = []
    dict_notes: List[str] = []
    noted: set = set()
    body = target[0] if target else None

    def sub(m: re.Match) -> str:
        word, ann = m.group("w"), m.group("a")
        out_ann = f"({ann})" if ann else ""
        upper = False
        for ch in word:
            if kb.entry(ch, body) is None:
                d = dictionary.lookup(ch) if dictionary else None
                if d is None:
                    unresolved.append(ch)
                elif ch not in noted:  # one note per char, however often the answer repeats it
                    noted.add(ch)
                    dict_notes.append(f"'{ch}'은(는) 우리말샘에서 찾았어요: {d}")
            elif target and not kb.within_target(target[0], target[1], ch):
                upper = True
        if ann and len(word) == 1 and kb.entry(word, body):
            if " " in ann or len(ann) == 1:
                e = kb.entry(word, body)
                out_ann = f"({e['hun'].strip()} {e['eum'].strip()})"
        return word + out_ann + (UPPER_MARK if upper else "")

    text = _TOKEN_RE.sub(sub, reply)
    if not shape:
        return text, unresolved, dict_notes
    sents = [s for s in _SENT_SPLIT.split(text.strip()) if s]
    sents = sents[:MAX_SENTENCES]
    i = 0
    while len(sents) < MIN_SENTENCES:
        sents.append(FILLER[i % len(FILLER)])
        i += 1
    return " ".join(sents), unresolved, dict_notes


class ChatbotService:
    def __init__(
        self,
        accounts: AccountService,
        kb: HanjaKnowledge,
        llm: LLMProvider,
        dictionary: Optional[DictionaryClient] = None,
        notifier: Optional[Notifier] = None,
        clock: Callable[[], datetime] = lambda: datetime.now(KST),
        use_llm_classifier: bool = True,
        usage=None,  # usage.UsageService; None = unmetered
        classifier_llm: Optional[LLMProvider] = None,  # defaults to `llm`
    ) -> None:
        self.usage = usage
        self.accounts, self.kb, self.llm, self.dictionary = accounts, kb, llm, dictionary
        self.notifier = notifier or RecordingNotifier()
        self.clock = clock
        self.classifier = LLMClassifier(classifier_llm or llm) if use_llm_classifier else None
        self.events: List[SafetyEvent] = []
        self._thread_counts: Dict[str, int] = {}
        self._locks: set = set()  # (child_id, KST date)

    # ---- helpers ----------------------------------------------------------
    def _today(self) -> date:
        return self.clock().astimezone(KST).date()

    def is_locked(self, child_id: str) -> bool:
        return (child_id, self._today()) in self._locks

    def _target(self, child_id: str) -> Optional[tuple]:
        t = self.accounts.children[child_id].extra.get("target")
        return tuple(t) if t else None

    def _redirect_reply(self, child_id: str) -> str:
        t = self._target(child_id)
        sug = self.kb.suggest_words(t[0], t[1], 1) if t else []
        hint = f" 예를 들어 '{sug[0]}' 같은 한자(어)를 같이 알아볼까요?" if sug else " 대신 한자나 낱말 뜻을 같이 알아볼까요?"
        return "그건 여기서 도와줄 수 없는 이야기예요." + hint

    # ---- main entry --------------------------------------------------------
    def ask(self, child_id: str, thread_id: str, message: str, history: Optional[List[dict]] = None) -> ChatResult:
        self.accounts.require_chatbot(child_id)  # raises ChatbotDisabled without AI consent
        if self.is_locked(child_id):
            return ChatResult("locked", LOCK_REPLY, locked_today=True)

        text = message or ""
        word = None
        gate = rule_gate(text)
        if gate is None:
            word = self.resolve_word(text)
            label = self.classifier.classify(text) if self.classifier is not None else None
            gate = gate_from_label(text, label, word_on_topic=word_on_topic(word))
        if gate.action == "block":
            return self._handle_block(child_id, thread_id, gate)
        if gate.action == "redirect":
            return ChatResult("redirected", self._redirect_reply(child_id), "off_topic")
        if self.usage is None:
            return self._answer(child_id, message, history or [], word)
        try:
            res = self.usage.consume(child_id, "chatbot")  # atomic; no concurrent overshoot
        except FeatureLocked:
            return ChatResult("locked", child_notice("chatbot"), locked_today=True)
        try:
            result = self._answer(child_id, message, history or [], word)
        except BaseException:
            self.usage.release(res)
            raise
        if not result.counts_as_answer:  # failed responses are free
            self.usage.release(res)
        return result

    def _handle_block(self, child_id: str, thread_id: str, gate: Gate) -> ChatResult:
        count = self._thread_counts.get(thread_id, 0) + 1
        self._thread_counts[thread_id] = count
        ev = SafetyEvent(uuid.uuid4().hex, child_id, thread_id, gate.category or "harmful", gate.source, self.clock(), count)
        parent_id = self.accounts.children[child_id].parent_id
        locked = count >= SAFETY_LOCK_THRESHOLD
        if locked:
            self._locks.add((child_id, self._today()))
        self.notifier.notify(
            parent_id, "safety_event",
            {"child_id": child_id, "event_id": ev.id, "category": ev.category, "thread_count": count, "locked_today": locked},
        )
        ev.parent_notified = True
        self.events.append(ev)  # message text deliberately not kept anywhere
        return ChatResult("blocked", LOCK_REPLY if locked else BLOCK_REPLY, ev.category, locked_today=locked)

    def parent_safety_view(self, parent_id: str, child_id: str) -> List[SafetyEvent]:
        child = self.accounts.children.get(child_id)
        if child is None:
            raise NotFound("child")
        if child.parent_id != parent_id:
            raise Forbidden("not your child profile")
        return [e for e in self.events if e.child_id == child_id]

    # ---- answering ----------------------------------------------------------
    def resolve_word(self, message: str):
        """Short Hangul word question -> (candidates, WordResolution | None, looked_up) via 우리말샘 원어."""
        try:
            return resolve_word(self.dictionary, message)
        except Exception:  # noqa: BLE001 - a dictionary problem never breaks the chat (treated as no hit)
            return [], None, False

    def _answer(self, child_id: str, message: str, history: List[dict], word=None) -> ChatResult:
        prep = self._prepare(child_id, message, history, word)
        if isinstance(prep, ChatResult):
            return prep
        return self._finish(prep, self._llm_text(prep))

    def _llm_text(self, prep: dict):
        try:
            raw = self.llm.complete(prep["system"], prep["msgs"])
        except LLMError:
            return None
        return raw if (raw or "").strip() else None

    def _finish(self, prep: dict, raw) -> ChatResult:
        if raw is None:
            return ChatResult("error", FAIL_REPLY)
        sources, notes, unresolved = prep["sources"], prep["notes"], prep["unresolved"]
        text, bad, dict_notes = postprocess(raw, self.kb, prep["target"], self.dictionary)
        # a char already explained by a note from _prepare is not explained twice
        dict_notes = [n for n in dict_notes if not any(n.startswith(f"'{c}'") for c in prep.get("noted", ()))]
        if bad:
            return ChatResult("unknown", UNKNOWN_REPLY)
        if dict_notes:
            sources.append("urimalsaem")
        if unresolved:
            notes.append("나머지 글자는 잘 모르겠어요.")
        sources = ["db"] + [s for s in dict.fromkeys(sources)]
        return ChatResult("answered", " ".join([text] + dict_notes + notes), sources=sources)

    def _prepare(self, child_id: str, message: str, history: List[dict], word=None):
        """Either a final ChatResult (no LLM needed) or a dict describing the LLM call.

        word: resolve_word(message) when the caller already has it (else looked up here, cached)."""
        target = self._target(child_id)
        body = target[0] if target else None
        cands, res, _ = word if word is not None else self.resolve_word(message)
        q_chars = list(dict.fromkeys(HANJA_RE.findall(message)))
        sources: List[str] = []
        notes: List[str] = []
        word_lines: List[str] = []
        if res is not None and res.is_hanja:
            alts = [a for a in res.alternatives if all(self.kb.entry(c, body) for c in a)]
            q_chars = list(dict.fromkeys(q_chars + list(res.hanja) + [c for a in alts for c in a]))
            line = f"학생이 묻는 낱말: {res.word}({res.hanja})"
            if alts:
                line += " / 같은 소리의 다른 한자어: " + ", ".join(alts)
            word_lines.append(line)
            if res.definition:
                word_lines.append(f"우리말샘 뜻풀이: {res.definition}")
            word_lines.append(f"'{res.word}'이(가) 어떤 한자로 이루어졌는지 한 글자씩 DB 훈·음으로 풀어 낱말 뜻을 쉽게 설명한다.")
            sources.append("urimalsaem")
        elif res is not None:
            word_lines.append(f"학생이 묻는 낱말: {res.word} (우리말샘에 한자 원어가 없는 우리말"
                              + (f", 뜻풀이: {res.definition}" if res.definition else "") + ")")
            word_lines.append("이 낱말에 한자를 지어 붙이지 말고, 한자가 없는 우리말이라고 쉽게 설명한다.")
            sources.append("urimalsaem")
        elif cands and not q_chars:  # no dictionary hit / no API key: list per-syllable DB hanja, never invent a word
            stem = cands[1] if len(cands) > 1 and len(cands[1]) >= 2 else cands[0]
            per = syllable_candidates(self.kb, target, stem)
            if per:
                return ChatResult("answered", self._syllable_reply(stem, per, target), sources=["db"])
        known = [c for c in q_chars if self.kb.entry(c, body)]
        unresolved: List[str] = []
        missing = [c for c in q_chars if c not in known]
        found = lookup_many(self.dictionary, missing) if self.dictionary else {}
        noted: set = set()
        for c in missing:
            d = found.get(c)
            if d:
                noted.add(c)
                notes.append(f"'{c}'은(는) 우리 한자 목록에는 없어서 우리말샘에서 찾았어요: {d}")
                sources.append("urimalsaem")
            else:
                unresolved.append(c)
        if q_chars and not known:
            if notes:  # answer fully from 우리말샘
                return ChatResult("answered", " ".join(notes), sources=sources)
            return ChatResult("unknown", UNKNOWN_REPLY)

        ctx = []
        for c in known:
            e = self.kb.entry(c, body)
            tag = "" if not target or self.kb.within_target(target[0], target[1], c) else " (상위 급수)"
            ctx.append(f"{c}: 훈 {e['hun'].strip()}, 음 {e['eum'].strip()}{tag}")
        system = (
            "너는 초등학생에게 한자와 어휘를 알려 주는 선생님이다. 한자·어휘·교과 어휘 이야기만 한다. "
            "초등학생이 이해할 쉬운 말로 3~5문장으로 답한다. 한자의 훈·음은 아래 DB 값만 쓰고 "
            "'漢(한나라 한)' 형식으로 쓴다. 목표 급수 안의 한자를 우선 쓴다.\n"
            + "".join(line + "\n" for line in word_lines) + "[DB]\n" + "\n".join(ctx)
        )
        msgs = list(history) + [{"role": "user", "content": message}]
        return {"system": system, "msgs": msgs, "target": target, "sources": sources,
                "notes": notes, "unresolved": unresolved, "noted": noted}

    def _syllable_reply(self, stem: str, per, target: tuple) -> str:
        parts = []
        for syl, chars in per:
            shown = []
            for c in chars:
                e = self.kb.entry(c, target[0])
                mark = "" if self.kb.within_target(target[0], target[1], c) else UPPER_MARK
                shown.append(f"{c}({e['hun'].strip()} {e['eum'].strip()}){mark}")
            parts.append(f"{syl} - " + ", ".join(shown))
        return (f"'{stem}'을(를) 어떤 한자로 쓰는지는 사전에서 확인하지 못했어요. "
                f"글자마다 이런 한자가 있어요: {' / '.join(parts)}. "
                "어떤 낱말인지 한자로 써서 다시 물어봐 주세요.")

    # ---- streaming ------------------------------------------------------------
    def ask_stream(self, child_id: str, thread_id: str, message: str, history: Optional[List[dict]] = None,
                   timeout: float = 15.0):
        from .streaming import stream_ask
        return stream_ask(self, child_id, thread_id, message, history, timeout)
