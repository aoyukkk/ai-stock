from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from llm_gateway.schemas import LLMResponse


@dataclass
class CacheEntry:
    value: LLMResponse
    expires_at: datetime


class InMemoryLLMCache:
    def __init__(self) -> None:
        self._items: dict[str, CacheEntry] = {}

    def get(self, key: str) -> LLMResponse | None:
        entry = self._items.get(key)
        if entry is None:
            return None
        if entry.expires_at <= datetime.now(timezone.utc):
            self._items.pop(key, None)
            return None
        return entry.value.model_copy(deep=True)

    def set(self, key: str, value: LLMResponse, ttl_seconds: int) -> None:
        self._items[key] = CacheEntry(
            value=value.model_copy(deep=True),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
        )

    def clear(self) -> None:
        self._items.clear()
