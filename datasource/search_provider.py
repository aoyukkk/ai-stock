from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from datasource.interfaces.search_provider import SearchProvider
from market_review.schemas import SearchResult


class SearchProviderUnavailable(RuntimeError):
    pass


class DisabledSearchProvider(SearchProvider):
    name = "NONE"
    version = "disabled-v1"
    requires_api_key = False

    def search(self, query: str, start_time: datetime, end_time: datetime, language: str, max_results: int, domain_filter: list[str] | None = None) -> list[SearchResult]:
        raise SearchProviderUnavailable("SEARCH_PROVIDER_NOT_CONFIGURED")


class MockMarketSearchProvider(SearchProvider):
    name = "MOCK"
    version = "mock-market-search-v1"
    requires_api_key = False

    def __init__(self, results: list[SearchResult] | None = None) -> None:
        self.results = results or []
        self.call_count = 0

    def search(self, query: str, start_time: datetime, end_time: datetime, language: str, max_results: int, domain_filter: list[str] | None = None) -> list[SearchResult]:
        self.call_count += 1
        return list(self.results[:max_results])


class TavilySearchProvider(SearchProvider):
    """Tavily Search adapter. Calls remain disabled unless explicitly allowed."""

    name = "TAVILY"
    version = "tavily-search-v1"
    requires_api_key = True

    def __init__(
        self,
        *,
        api_key: str | None = None,
        allow_external_calls: bool = False,
        transport: Callable[[dict[str, Any], dict[str, str]], dict[str, Any]] | None = None,
    ) -> None:
        self._api_key = (api_key or os.getenv("TAVILY_API_KEY", "")).strip()
        self.allow_external_calls = allow_external_calls
        self.transport = transport or self._http_transport

    def search(self, query: str, start_time: datetime, end_time: datetime, language: str, max_results: int, domain_filter: list[str] | None = None) -> list[SearchResult]:
        if not self._api_key:
            raise SearchProviderUnavailable("TAVILY_NOT_CONFIGURED")
        if not self.allow_external_calls:
            raise SearchProviderUnavailable("REAL_SEARCH_CALLS_NOT_APPROVED")
        body: dict[str, Any] = {
            "query": query,
            "topic": "news",
            "search_depth": "basic",
            "max_results": max_results,
            "include_answer": False,
            "include_raw_content": False,
        }
        if domain_filter:
            body["include_domains"] = domain_filter
        payload = self.transport(body, {"Authorization": f"Bearer {self._api_key}"})
        fetched_at = datetime.now(timezone.utc)
        items = []
        for index, row in enumerate(payload.get("results") or []):
            url = str(row.get("url") or "")
            if not url.startswith(("https://", "http://")):
                continue
            publish_time = _parse_time(row.get("published_date"))
            items.append(SearchResult(
                title=str(row.get("title") or "").strip()[:512],
                url=url,
                domain=(urlparse(url).hostname or "").lower(),
                source_name=(urlparse(url).hostname or "Tavily").lower(),
                publish_time=publish_time,
                fetched_at=fetched_at,
                snippet=str(row.get("content") or "").strip()[:1000],
                source_type="WEB_SEARCH",
                language=language,
                provider=self.name,
                provider_result_id=str(row.get("id") or hashlib.sha256(f"{query}:{index}:{url}".encode()).hexdigest()[:24]),
            ))
        return [item for item in items if _within(item.publish_time, start_time, end_time)]

    @staticmethod
    def _http_transport(body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        with httpx.Client(timeout=20.0) as client:
            response = client.post("https://api.tavily.com/search", json=body, headers=headers)
            response.raise_for_status()
            return dict(response.json())


def build_market_search_provider(name: str, *, allow_external_calls: bool = False) -> SearchProvider:
    normalized = name.strip().upper()
    if normalized in {"AUTO", "TAVILY"} and os.getenv("TAVILY_API_KEY"):
        return TavilySearchProvider(allow_external_calls=allow_external_calls)
    if normalized == "MOCK":
        return MockMarketSearchProvider()
    return DisabledSearchProvider()


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _within(value: datetime | None, start: datetime, end: datetime) -> bool:
    if value is None:
        return True
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return start <= value <= end
