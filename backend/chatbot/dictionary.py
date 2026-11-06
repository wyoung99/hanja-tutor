"""Fallback dictionary: 우리말샘 open API.

Two uses:
* ``lookup(term)``: a definition for a term the reviewed DB lacks (hanja missing from the DB).
* ``search(term)``: the raw entries for a short Hangul word query. Each sense carries its 원어 in the field
  ``origin`` (e.g. 학교 -> "學校", 사과 -> "沙果/砂果"; a native word has ``origin == ""``); the chatbot uses it to
  resolve "학교" to 學校 (chatbot.wordquery).

Results are cached in-process (small LRU per client, successful responses only) so repeated short-word
questions add no dictionary latency before the answer stream.
"""
from __future__ import annotations

import json
import threading
import urllib.parse
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Protocol

URIMALSAEM_URL = "https://opendict.korean.go.kr/api/search"
CACHE_SIZE = 256


@dataclass(frozen=True)
class DictEntry:
    """One 우리말샘 sense. ``origin`` is the 원어 field (``sense.origin``), "" for a native word."""
    word: str
    origin: str = ""
    definition: str = ""
    cat: str = ""        # 전문 분야 ("" = general vocabulary; 지명/책명/... = proper names and works)
    pos: str = ""
    sense_no: str = ""


class DictionaryClient(Protocol):
    def lookup(self, term: str) -> Optional[str]:
        """Return a definition for the term, or None if not found."""


class _LRU:
    def __init__(self, size: int = CACHE_SIZE) -> None:
        self.size = size
        self._d: "OrderedDict[str, object]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str):
        with self._lock:
            if key not in self._d:
                return None
            self._d.move_to_end(key)
            return self._d[key]

    def put(self, key: str, value) -> None:
        with self._lock:
            self._d[key] = value
            self._d.move_to_end(key)
            while len(self._d) > self.size:
                self._d.popitem(last=False)


def parse_entries(data: dict) -> List[DictEntry]:
    items = (data.get("channel") or {}).get("item") or []
    if isinstance(items, dict):
        items = [items]
    out: List[DictEntry] = []
    for it in items:
        senses = it.get("sense")
        senses = senses if isinstance(senses, list) else [senses]
        for s in senses:
            if not isinstance(s, dict):
                continue
            out.append(DictEntry(str(it.get("word") or ""), str(s.get("origin") or "").strip(),
                                 str(s.get("definition") or "").strip(), str(s.get("cat") or "").strip(),
                                 str(s.get("pos") or "").strip(), str(s.get("sense_no") or "").strip()))
    return out


class UrimalsaemClient:
    def __init__(self, api_key: str, http_get: Callable[[str], str] | None = None, timeout: float = 5.0,
                 cache_size: int = CACHE_SIZE):
        self.api_key = api_key
        self.timeout = timeout
        self._get = http_get or self._default_get
        self._cache = _LRU(cache_size)

    def _default_get(self, url: str) -> str:
        with urllib.request.urlopen(url, timeout=self.timeout) as r:  # noqa: S310 - fixed https host
            return r.read().decode("utf-8")

    def search(self, term: str) -> Optional[List[DictEntry]]:
        """All senses for term (API order), [] when nothing matches, None on network/parse failure."""
        hit = self._cache.get(term)
        if hit is not None:
            return list(hit)
        qs = urllib.parse.urlencode({"key": self.api_key, "q": term, "req_type": "json", "num": 10})  # API rejects num < 10
        try:
            entries = parse_entries(json.loads(self._get(f"{URIMALSAEM_URL}?{qs}")))
        except Exception:  # noqa: BLE001 - network/parse failure == not found (not cached)
            return None
        self._cache.put(term, tuple(entries))
        return entries

    def lookup(self, term: str) -> Optional[str]:
        for e in self.search(term) or []:
            if e.definition:
                return e.definition
        return None


class FakeDictionary:
    """entries: term -> definition (``lookup``); words: term -> DictEntry list (``search``)."""

    def __init__(self, entries: dict[str, str] | None = None, words: Dict[str, List[DictEntry]] | None = None):
        self.entries = entries or {}
        self.words = words or {}
        self.queries: list[str] = []
        self.searches: list[str] = []

    def lookup(self, term: str) -> Optional[str]:
        self.queries.append(term)
        return self.entries.get(term)

    def search(self, term: str) -> Optional[List[DictEntry]]:
        self.searches.append(term)
        return list(self.words.get(term, []))
