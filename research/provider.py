from __future__ import annotations

from typing import Protocol

from research.schemas import ResearchQuery, ResearchResult


class ResearchProvider(Protocol):
    @property
    def name(self) -> str: ...

    def search(self, request: ResearchQuery) -> ResearchResult: ...
