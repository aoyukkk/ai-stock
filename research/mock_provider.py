from __future__ import annotations

from research.schemas import ResearchEvidence, ResearchQuery, ResearchResult, ResearchStatus


class MockResearchProvider:
    def __init__(self, evidence: list[ResearchEvidence] | None = None) -> None:
        self._evidence = evidence or []

    @property
    def name(self) -> str:
        return "mock_research"

    def search(self, request: ResearchQuery) -> ResearchResult:
        evidence = [item for item in self._evidence if item.stock_code == request.stock_code]
        return ResearchResult(
            status=ResearchStatus.VERIFIED if evidence else ResearchStatus.NO_VERIFIED_EVIDENCE,
            provider=self.name,
            stock_code=request.stock_code,
            query=request.query,
            evidence=evidence[: request.max_sources],
        )
