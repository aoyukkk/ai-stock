from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SourceStatus(StrEnum):
    VERIFIED_STRUCTURED = "VERIFIED_STRUCTURED"
    DERIVED_RULE = "DERIVED_RULE"
    WEB_VERIFIED = "WEB_VERIFIED"
    MANUAL_EVIDENCE = "MANUAL_EVIDENCE"
    LLM_UNVERIFIED = "LLM_UNVERIFIED"
    UNKNOWN = "UNKNOWN"


class ProvenancedField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: Any = None
    source_status: SourceStatus
    display_marker: str = ""
    verified: bool
    confidence: float = Field(ge=0, le=1)
    fallback_reason: str | None = None
    requires_manual_review: bool = False
    evidence_fields: list[str] = Field(default_factory=list)


def unverified_field(value: Any, confidence: float, reason: str) -> ProvenancedField:
    return ProvenancedField(
        value=value,
        source_status=SourceStatus.LLM_UNVERIFIED,
        display_marker="*",
        verified=False,
        confidence=confidence,
        fallback_reason=reason,
        requires_manual_review=True,
    )


class FinancialStatusResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    profitability_status: str
    growth_status: str
    cash_flow_status: str
    leverage_status: str
    receivable_risk: str
    inventory_risk: str
    goodwill_risk: str
    audit_risk: str
    shareholder_action_risk: str
    reason_codes: list[str]
    evidence_fields: list[str]
    as_of_period: str | None = None
    available_at: datetime | None = None
    source_status: SourceStatus = SourceStatus.DERIVED_RULE
    rule_profile: str = "INSUFFICIENT_CLASSIFICATION"
    industry_classification_source: str = "UNKNOWN"
    applicable_metrics: list[str] = Field(default_factory=list)
    excluded_metrics: list[str] = Field(default_factory=list)
    missing_industry_metrics: list[str] = Field(default_factory=list)
