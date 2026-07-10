from __future__ import annotations

from enum import StrEnum

from temporal.schemas import RunMode


class LLMKnowledgeMode(StrEnum):
    STRUCTURED_INPUT_ONLY = "STRUCTURED_INPUT_ONLY"
    LLM_UNVERIFIED_CURRENT = "LLM_UNVERIFIED_CURRENT"
    WEB_VERIFIED = "WEB_VERIFIED"


def validate_knowledge_mode(run_mode: RunMode | str, knowledge_mode: LLMKnowledgeMode | str) -> None:
    mode = RunMode(run_mode)
    knowledge = LLMKnowledgeMode(knowledge_mode)
    if mode == RunMode.HISTORICAL_REPLAY and knowledge != LLMKnowledgeMode.STRUCTURED_INPUT_ONLY:
        raise ValueError("HISTORICAL_RUN_CANNOT_USE_UNBOUNDED_MODEL_KNOWLEDGE")
    if mode in {RunMode.POST_MARKET_FINAL, RunMode.PRE_MARKET_RECHECK} and knowledge == LLMKnowledgeMode.LLM_UNVERIFIED_CURRENT:
        raise ValueError("CURRENT_RUN_REQUIRES_STRUCTURED_OR_WEB_VERIFIED_KNOWLEDGE")
