from __future__ import annotations

from typing import Any

from research.capability import CAPABILITY_NOT_AVAILABLE, DeepSeekWebCapabilityProbe
from research.schemas import ResearchQuery, ResearchResult, ResearchStatus


class DeepSeekWebResearchProvider:
    """Temporary fail-closed provider; it never bypasses the existing LLM gateway."""

    def __init__(self, gateway: Any | None = None) -> None:
        self.gateway = gateway
        self.capability = DeepSeekWebCapabilityProbe()

    @property
    def name(self) -> str:
        return "deepseek_web"

    def search(self, request: ResearchQuery) -> ResearchResult:
        probe = self.capability.run()
        if probe["status"] == CAPABILITY_NOT_AVAILABLE:
            return ResearchResult(
                status=ResearchStatus.CAPABILITY_NOT_AVAILABLE,
                provider=self.name,
                stock_code=request.stock_code,
                query=request.query,
                warnings=[probe["reason"]],
                provider_metadata=probe,
            )
        raise RuntimeError("DeepSeek web research must use the configured LLM Gateway")
