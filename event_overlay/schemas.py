from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from event_overlay.constants import (
    DECISION_VERSION,
    DIRECT_SEARCH_FALLBACK,
    EVENT_REVIEW_VERSION,
    PRODUCTION_OR_SHADOW,
    SCREENING_VERSION,
    SEARCH_CONTRACT_VERSION,
)


class RiskAction(StrEnum):
    ALLOW = "ALLOW"
    PROMOTE = "PROMOTE"
    KEEP = "KEEP"
    DEMOTE = "DEMOTE"
    WATCH_ONLY = "WATCH_ONLY"
    BLOCK = "BLOCK"


class EventEvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stock_code: str
    event_id: str
    event_cluster_id: str
    event_type: str
    title: str = Field(max_length=500)
    summary: str = Field(max_length=2000)
    url: str | None = None
    canonical_url: str | None = None
    domain: str | None = None
    published_at: datetime | None = None
    retrieved_at: datetime
    source_tier: str = Field(pattern=r"^tier_[1-4]$")
    source_type: str
    provider: str
    provider_verified: bool
    event_direction: str = Field(pattern=r"^(POSITIVE|NEGATIVE|NEUTRAL|MIXED)$")
    direction: float = Field(default=0, ge=-1, le=1)
    impact_magnitude: float | None = Field(default=None, ge=0, le=1)
    materiality: float = Field(ge=0, le=1)
    relevance: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    named_company: bool | None = None
    directness: float | None = Field(default=None, ge=0, le=1)
    directness_reason: str | None = None
    exposure_estimate: float | None = Field(default=None, ge=0, le=1)
    exposure_confidence: float | None = Field(default=None, ge=0, le=1)
    exposure_evidence: str | None = None
    novelty: float | None = Field(default=None, ge=0, le=1)
    price_already_reacted: float | None = Field(default=None, ge=0, le=1)
    confirmation_status: str | None = Field(
        default=None,
        pattern=r"^(CONFIRMED|PARTIAL|NOT_CONFIRMED|NOT_AVAILABLE_AT_CUTOFF)$",
    )
    a_share_breadth_confirmation: float | None = Field(default=None, ge=0, le=1)
    crowding_status: str = Field(default="UNKNOWN", pattern=r"^(LOW|MEDIUM|HIGH|UNKNOWN)$")
    contradiction_status: str = Field(default="NONE", pattern=r"^(NONE|PARTIAL|CONFLICT)$")
    decay_days: float | None = Field(default=None, ge=0)
    time_decay: float | None = Field(default=None, ge=0, le=1)
    point_in_time_safe: bool = True
    temporal_status: str
    score_eligible: bool = True
    score_exclusion_reasons: list[str] = Field(default_factory=list)
    freshness_window_hours: float | None = Field(default=None, gt=0)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    revision: int = Field(default=1, ge=1)
    raw_metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def url_consistency(self):
        if self.url is not None and not self.url.startswith(("http://", "https://")):
            raise ValueError("EVENT_URL_INVALID")
        if self.canonical_url is not None and not self.canonical_url.startswith(("http://", "https://")):
            raise ValueError("EVENT_CANONICAL_URL_INVALID")
        return self


class EventReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stock_code: str
    resolved_stock_name: str | None = None
    search_status: str
    material_events: list[EventEvidenceItem] = Field(default_factory=list, max_length=5)
    event_opportunity_score: float = Field(ge=-3, le=3)
    evidence_confidence: float = Field(ge=0, le=1)
    breadth_score: float = Field(ge=0, le=1)
    risk_action: RiskAction
    conflicts: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    requires_pro_review: bool = False
    direct_search_used: bool = False
    provider_verified: bool = False
    confidence_discount_applied: bool = False
    production_eligible: bool = False
    shadow_eligible: bool = True
    review_version: str = EVENT_REVIEW_VERSION

    @model_validator(mode="after")
    def fallback_contract(self):
        if self.direct_search_used or self.search_status == DIRECT_SEARCH_FALLBACK:
            expected = (
                self.direct_search_used
                and not self.provider_verified
                and self.confidence_discount_applied
                and not self.production_eligible
                and self.shadow_eligible
            )
            if not expected:
                raise ValueError("DIRECT_SEARCH_FALLBACK_CONTRACT_INVALID")
        if self.risk_action == RiskAction.BLOCK and self.event_opportunity_score > 0:
            raise ValueError("BLOCK_CANNOT_HAVE_POSITIVE_EVENT_SCORE")
        if any(
            item.stock_code != self.stock_code
            for item in self.material_events
        ):
            raise ValueError("EVENT_REVIEW_STOCK_CODE_MISMATCH")
        return self


class EventEvidenceSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str
    run_id: str
    trade_date: date
    decision_as_of_time: datetime
    stock_code: str
    query: str
    search_status: str
    provider: str
    provider_verified: bool
    direct_search_used: bool
    confidence_discount_applied: bool
    production_eligible: bool = False
    shadow_eligible: bool = True
    items: list[EventEvidenceItem] = Field(default_factory=list)
    review_conflicts: list[str] = Field(default_factory=list)
    review_warnings: list[str] = Field(default_factory=list)
    input_hash: str
    content_hash: str
    immutable: bool = True
    contract_version: str = SEARCH_CONTRACT_VERSION

    @field_validator("decision_as_of_time")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("DECISION_AS_OF_TIME_REQUIRES_TIMEZONE")
        return value

    @model_validator(mode="after")
    def item_stock_codes_match_snapshot(self):
        if any(item.stock_code != self.stock_code for item in self.items):
            raise ValueError("EVENT_SNAPSHOT_STOCK_CODE_MISMATCH")
        return self


class ScreeningItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stock_code: str
    stock_name: str
    quant_rank: int = Field(ge=1)
    quant_score: float
    event_opportunity_score: float = Field(ge=-3, le=3)
    evidence_confidence: float = Field(ge=0, le=1)
    evidence_breadth: float = Field(ge=0, le=1)
    risk_action: RiskAction
    v3_screening_score: float = Field(ge=0, le=100)
    v3_rank: int | None = None
    selected_top20: bool = False
    search_status: str
    event_snapshot_id: str
    checkpoint_status: str
    hard_gate_reasons: list[str] = Field(default_factory=list)
    raw_quant: dict[str, Any] = Field(default_factory=dict)


class ScreeningRunManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    trade_date: date
    decision_as_of_time: datetime
    source_run_id: str
    source_input_hash: str
    universe_snapshot_id: str
    factor_version: str
    screening_version: str = SCREENING_VERSION
    decision_version: str = DECISION_VERSION
    event_review_version: str = EVENT_REVIEW_VERSION
    search_contract_version: str = SEARCH_CONTRACT_VERSION
    production_or_shadow: str = PRODUCTION_OR_SHADOW
    execution_mode: str
    real_search_enabled: bool
    historical_replay: bool
    target_trade_date: date | None = None
    input_count: int
    output_count: int
    actual_network_calls: int
    web_search_request_count: int = 0
    logical_evaluations: int
    successful_evaluation_count: int = 0
    reused_checkpoint_count: int
    stale_checkpoint_count: int
    real_orders: int = 0
    virtual_orders: int = 0
    scheduler: bool = False
    content_hash: str
