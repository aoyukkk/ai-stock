from __future__ import annotations

from dataclasses import dataclass


LLM_SHADOW_SCORE_VERSION = "llm_structured_score_v3_shadow_1"


@dataclass(frozen=True)
class LLMShadowStructuredOutput:
    sector_catalyst_score: float
    business_quality_score: float
    risk_score: float
    holding_period_fit: float
    evidence_confidence: float


@dataclass(frozen=True)
class LocalLLMScore:
    final_llm_score: float
    version: str = LLM_SHADOW_SCORE_VERSION


def calculate_local_llm_score(value: LLMShadowStructuredOutput) -> LocalLLMScore:
    """Local deterministic score; the LLM has no ranking or final-score authority."""

    fields = (
        value.sector_catalyst_score,
        value.business_quality_score,
        value.risk_score,
        value.holding_period_fit,
        value.evidence_confidence,
    )
    if any(not 0 <= field <= 100 for field in fields):
        raise ValueError("INVALID_LLM_SHADOW_STRUCTURED_OUTPUT")
    score = (
        value.sector_catalyst_score * 0.25
        + value.business_quality_score * 0.25
        + value.risk_score * 0.20
        + value.holding_period_fit * 0.15
        + value.evidence_confidence * 0.15
    )
    return LocalLLMScore(final_llm_score=round(score, 6))
