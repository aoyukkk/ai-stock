from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from market_review.schemas import SearchResult


class SearchProvider(ABC):
    name: str
    version: str
    requires_api_key: bool

    @abstractmethod
    def search(
        self,
        query: str,
        start_time: datetime,
        end_time: datetime,
        language: str,
        max_results: int,
        domain_filter: list[str] | None = None,
    ) -> list[SearchResult]:
        raise NotImplementedError
