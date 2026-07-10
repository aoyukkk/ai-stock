from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


Direction = Literal["BUY", "WATCH", "NEUTRAL", "AVOID"]
SampleMode = Literal["STRATIFIED", "TOP_N"]
ScreeningDecision = Literal["ADVANCE", "HOLD", "REJECT", "WATCH_ONLY"]


class ScreeningModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class LightScreeningInput(ScreeningModel):
    stock_code: str
    stock_name: str
    industry: str | None = None
    quant_rank: int
    quant_total_score: Decimal
    technical_score: Decimal
    capital_score: Decimal
    emotion_score: Decimal
    momentum_score: Decimal
    risk_score: Decimal
    quant_reason: str
    latest_news_summary: str | None = None
    overseas_summary: str | None = None
    liquidity_summary: str | None = None
    fundamental_profile_verified: bool = False
    fundamental_evidence_count: int = Field(default=0, ge=0)
    fundamental_profile: dict[str, Any] | None = None
    fundamental_missing_fields: list[str] = Field(default_factory=list)


class LightScreeningLLMOutput(ScreeningModel):
    stock_code: str
    opportunity_score: Decimal = Field(ge=0, le=100)
    event_catalyst_score: Decimal = Field(ge=0, le=100)
    sector_strength_score: Decimal = Field(ge=0, le=100)
    order_friendliness_score: Decimal = Field(ge=0, le=100)
    liquidity_score: Decimal = Field(ge=0, le=100)
    risk_penalty_score: Decimal = Field(ge=0, le=100)
    confidence: Decimal = Field(ge=0, le=1)
    direction: Direction
    reason: str
    risk_note: str
    should_keep: bool
    data_conflict: bool = False


class LightScreeningResult(ScreeningModel):
    stock_code: str
    stock_name: str
    industry: str | None = None
    quant_rank: int
    quant_total_score: Decimal
    llm_score: Decimal = Field(ge=0, le=100)
    final_light_score: Decimal = Field(ge=0, le=100)
    rank: int | None = None
    direction: Direction
    confidence: Decimal = Field(ge=0, le=1)
    should_keep: bool
    data_conflict: bool = False
    reason: str
    risk_note: str
    prompt_version: str | None = None
    model_name: str | None = None
    request_hash: str | None = None


class LightScreeningRanking(ScreeningModel):
    generated_at: datetime
    quant_universe_size: int
    requested_quant_top_q: int
    requested_top_n: int
    returned_count: int
    results: list[LightScreeningResult]


class RealLightScreeningInput(ScreeningModel):
    stock_code: str
    stock_name: str
    trade_date: str
    quant_run_id: str
    quant_rank: int
    quant_score: Decimal
    technical_score: Decimal
    capital_score: Decimal
    emotion_score: Decimal
    momentum_score: Decimal
    risk_score: Decimal
    technical_summary: str
    capital_summary: str
    emotion_summary: str
    momentum_summary: str
    risk_summary: str
    factor_detail_summary: list[dict[str, Any]]
    limit_status: str
    near_limit_up: bool | None
    near_limit_down: bool | None
    consecutive_limit_up: int | None
    data_coverage: dict[str, Any]
    data_quality: str
    data_sources: list[str]
    snapshot_time: str
    news_summary: str | None = None
    news_available: bool = False
    known_missing_fields: list[str]
    adj_factor_coverage: bool
    adjusted_technical_factor_applied: bool = False
    limit_risk_explanation_available: bool
    limit_risk_weight_applied: bool = False
    fundamental_profile_verified: bool = False
    fundamental_evidence_count: int = Field(default=0, ge=0)
    fundamental_profile: dict[str, Any] | None = None
    fundamental_missing_fields: list[str] = Field(default_factory=list)
    financial_status: dict[str, Any] | None = None
    unverified_fundamental_inference: dict[str, Any] | None = None
    research_degradation_level: str = "UNKNOWN"
    data_conflict: bool = False


class RealLightScreeningOutput(ScreeningModel):
    stock_code: str
    quant_rank: int
    screening_decision: ScreeningDecision
    llm_score: Decimal = Field(ge=0, le=100)
    short_term_opportunity: Decimal = Field(ge=0, le=100)
    factor_consistency: Decimal = Field(ge=0, le=100)
    capital_confirmation: Decimal = Field(ge=0, le=100)
    emotion_confirmation: Decimal = Field(ge=0, le=100)
    risk_score: Decimal = Field(ge=0, le=100)
    data_quality_score: Decimal = Field(ge=0, le=100)
    confidence: Decimal = Field(ge=0, le=1)
    reason: str
    risk_note: str
    data_conflict: bool
    missing_data: list[str]
    evidence_fields: list[str]
    verified_field_count: int = Field(default=0, ge=0)
    derived_field_count: int = Field(default=0, ge=0)
    unverified_field_count: int = Field(default=0, ge=0)
    unknown_field_count: int = Field(default=0, ge=0)
    research_degradation_level: str = "UNKNOWN"
    fundamental_observation_rating: str = "INSUFFICIENT_DATA"
    requires_manual_review: bool = False

    @model_validator(mode="after")
    def unverified_fields_cannot_drive_advance(self):
        if (
            self.screening_decision == "ADVANCE"
            and self.unverified_field_count > 0
            and self.verified_field_count + self.derived_field_count == 0
        ):
            self.screening_decision = "HOLD"
            self.confidence = min(self.confidence, Decimal("0.4"))
            self.requires_manual_review = True
        return self


class RealScreeningSampleResult(ScreeningModel):
    stock_code: str
    quant_rank: int
    quant_score: Decimal
    screening_decision: ScreeningDecision
    llm_score: Decimal
    confidence: Decimal
    data_conflict: bool
    missing_data: list[str]
    provider: str
    model_alias: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    cost_status: str
    cached: bool
    status: str


class RealScreeningRunResult(ScreeningModel):
    status: str
    dry_run: bool
    sample_mode: SampleMode
    sample_size: int
    quant_run_id: str
    sample_source: str
    input_fields: list[str]
    route: dict[str, Any]
    real_call_readiness: str
    real_or_mock: str
    run_id: str
    selected_stocks: list[dict[str, Any]]
    results: list[RealScreeningSampleResult]
    aggregate_usage: dict[str, int]
    aggregate_cost_usd: float | None
    aggregate_cost_status: str
    cache_status: dict[str, int]
