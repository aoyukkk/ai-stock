from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DataStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    PARTIAL = "PARTIAL"
    NOT_AVAILABLE = "NOT_AVAILABLE"


class IndexPerformance(StrictModel):
    index_code: str
    index_name: str
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    pre_close: float | None = None
    change_percent: float | None = None
    amount: float | None = None
    volume: float | None = None
    intraday_range_percent: float | None = None
    ma5: float | None = None
    ma20: float | None = None
    ma60: float | None = None
    atr14: float | None = None
    position_vs_ma5: float | None = None
    position_vs_ma20: float | None = None
    trend_state: str = "INSUFFICIENT_DATA"
    data_status: DataStatus = DataStatus.NOT_AVAILABLE
    source: str = "LOCAL_CACHE"


class SectorPerformance(StrictModel):
    rank: int = 0
    sector_code: str
    sector_name: str
    sector_type: Literal["INDUSTRY", "CONCEPT"]
    change_percent: float
    advancing_ratio: float
    limit_up_count: int
    amount: float
    capital_status: str = "NOT_AVAILABLE"
    evidence_count: int = 0
    member_count: int


class MarketDailySnapshotData(StrictModel):
    schema_version: str
    trade_date: date
    decision_time: datetime
    market_direction: Literal["UP", "DOWN", "FLAT", "MIXED"]
    indices: list[IndexPerformance]
    breadth: dict[str, Any]
    turnover: dict[str, Any]
    limit_structure: dict[str, Any]
    industries: list[SectorPerformance]
    concepts: list[SectorPerformance]
    style: dict[str, Any]
    capital: dict[str, Any]
    technical: dict[str, Any]
    source_status: dict[str, Any]
    metric_ids: list[str]
    data_quality_score: float = Field(ge=0, le=100)
    dataset_watermark_hash: str
    snapshot_hash: str


class MarketRegimeResult(StrictModel):
    market_regime: str
    regime_score: float = Field(ge=0, le=100)
    regime_confidence: float = Field(ge=0, le=1)
    supporting_metrics: list[str]
    conflicting_metrics: list[str]
    data_quality_score: float = Field(ge=0, le=100)
    version: str


class ScenarioRule(StrictModel):
    scenario_type: Literal["BASE", "BULL", "BEAR"]
    probability: int = Field(ge=0, le=100)
    title: str


class MarketOutlookResult(StrictModel):
    market_outlook_score: float = Field(ge=0, le=100)
    base_case_probability: int = Field(ge=0, le=100)
    bull_case_probability: int = Field(ge=0, le=100)
    bear_case_probability: int = Field(ge=0, le=100)
    probability_model_version: str
    components: dict[str, float]
    scenarios: list[ScenarioRule]

    @field_validator("bear_case_probability")
    @classmethod
    def validate_probability_sum(cls, value: int, info):
        data = info.data
        if "base_case_probability" in data and "bull_case_probability" in data:
            if data["base_case_probability"] + data["bull_case_probability"] + value != 100:
                raise ValueError("SCENARIO_PROBABILITY_SUM_INVALID")
        return value


class SearchQuery(StrictModel):
    query: str
    generated_reason: str
    trade_date: date
    provider: str
    result_count: int = 0
    executed_at: datetime | None = None


class SearchResult(StrictModel):
    title: str
    url: HttpUrl
    domain: str
    source_name: str
    publish_time: datetime | None = None
    fetched_at: datetime
    snippet: str
    source_type: str = "MEDIA"
    language: str = "zh-CN"
    provider: str
    provider_result_id: str


class MarketEvidence(StrictModel):
    evidence_id: str
    trade_date: date
    title: str
    source_name: str
    domain: str
    url: HttpUrl
    publish_time: datetime | None = None
    fetched_at: datetime
    summary: str
    source_tier: str
    official: bool
    direction: Literal["POSITIVE", "NEGATIVE", "NEUTRAL"] = "NEUTRAL"
    affected_scope: str = "A股市场"
    affected_sectors: list[str] = Field(default_factory=list)
    credibility_score: float = Field(ge=0, le=1)
    relevance_score: float = Field(ge=0, le=1)
    timeliness_score: float = Field(ge=0, le=1)
    final_evidence_score: float = Field(ge=0, le=1)
    duplicate_group_id: str | None = None
    status: str
    content_hash: str
    provider: str


class DriverWire(StrictModel):
    title: str
    direction: Literal["POSITIVE", "NEGATIVE", "NEUTRAL"]
    impact_strength: int = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    affected_scope: str = "A股市场"
    affected_sectors: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    metric_ids: list[str] = Field(default_factory=list)
    explanation: str


class OutlookScenarioWire(StrictModel):
    probability: int = Field(ge=0, le=100)
    title: str
    description: str
    supporting_reasons: list[str]
    triggers: list[str]
    invalidation_conditions: list[str]
    watch_items: list[str]


class TomorrowOutlookWire(StrictModel):
    base_case: OutlookScenarioWire
    bull_case: OutlookScenarioWire
    bear_case: OutlookScenarioWire


class MarketDailyReviewWireV1(StrictModel):
    schema_version: Literal["market_daily_review_wire_v1"]
    trade_date: date
    market_direction: Literal["UP", "DOWN", "FLAT", "MIXED"]
    market_regime: str
    headline: str
    market_summary: str
    breadth_summary: str
    turnover_summary: str
    style_summary: str
    confirmed_drivers: list[DriverWire]
    probable_explanations: list[DriverWire]
    structural_observations: list[DriverWire]
    tomorrow_outlook: TomorrowOutlookWire
    key_watch_items: list[str]
    main_risks: list[str]
    data_conflict: bool
    search_status: Literal["VERIFIED", "PARTIAL", "UNAVAILABLE", "DATA_ONLY"]
    confidence: float = Field(ge=0, le=1)
