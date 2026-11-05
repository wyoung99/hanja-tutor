"""Fallback dictionary: 우리말샘 open API (used only when the reviewed DB lacks the term)."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Callable, Optional, Protocol

URIMALSAEM_URL = "https://opendict.korean.go.kr/api/search"


class DictionaryClient(Protocol):
    def lookup(self, term: str) -> Optional[str]:
        """Return a definition for the term, or None if not found."""


class UrimalsaemClient:
    def __init__(self, api_key: str, http_get: Callable[[str], str] | None = None, timeout: float = 5.0):
        self.api_key = api_key
        self.timeout = timeout
        self._get = http_get or self._default_get

    def _default_get(self, url: str) -> str:
        with urllib.request.urlopen(url, timeout=self.timeout) as r:  # noqa: S310 - fixed https host
            return r.read().decode("utf-8")

    def lookup(self, term: str) -> Optional[str]:
        qs = urllib.parse.urlencode({"key": self.api_key, "q": term, "req_type": "json", "num": 1})
        try:
            data = json.loads(self._get(f"{URIMALSAEM_URL}?{qs}"))
        except Exception:  # noqa: BLE001 - network/parse failure == not found
            return None
        items = (data.get("channel") or {}).get("item") or []
        if isinstance(items, dict):
            items = [items]
        for it in items:
            sense = it.get("sense")
            sense = sense[0] if isinstance(sense, list) and sense else sense
            definition = sense.get("definition") if isinstance(sense, dict) else None
            if definition:
                return str(definition).strip()
        return None


class FakeDictionary:
    def __init__(self, entries: dict[str, str] | None = None):
        self.entries = entries or {}
        self.queries: list[str] = []

    def lookup(self, term: str) -> Optional[str]:
        self.queries.append(term)
        return self.entries.get(term)
