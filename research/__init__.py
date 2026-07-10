"""Auditable fundamental-research foundation with fail-closed real-web access."""

from research.capability import DeepSeekWebCapabilityProbe
from research.schemas import ResearchEvidence, ResearchQuery, ResearchResult

__all__ = [
    "DeepSeekWebCapabilityProbe",
    "ResearchEvidence",
    "ResearchQuery",
    "ResearchResult",
]
