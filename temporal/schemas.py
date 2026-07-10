from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RunMode(StrEnum):
    RESEARCH_ONLY = "RESEARCH_ONLY"
    HISTORICAL_REPLAY = "HISTORICAL_REPLAY"
    POST_MARKET_PRELIMINARY = "POST_MARKET_PRELIMINARY"
    POST_MARKET_FINAL = "POST_MARKET_FINAL"
    PRE_MARKET_RECHECK = "PRE_MARKET_RECHECK"
    INTRADAY_MONITOR = "INTRADAY_MONITOR"


class TemporalStatus(StrEnum):
    PASS = "PASS"
    PASS_WITH_WARNINGS = "PASS_WITH_WARNINGS"
    PROVISIONAL = "PROVISIONAL"
    BLOCKED = "BLOCKED"


class RunTemporalContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    run_mode: RunMode
    decision_time: datetime
    timezone: str = "Asia/Shanghai"
    market_session: str
    base_market_trade_date: date | None = None
    target_trade_date: date | None = None
    latest_completed_trade_date: date | None = None
    news_cutoff_time: datetime | None = None
    fundamental_cutoff_time: datetime
    realtime_snapshot_time: datetime | None = None
    allow_provisional: bool = False
    temporal_status: TemporalStatus = TemporalStatus.BLOCKED
    actionable: bool = False
    created_at: datetime

    @field_validator("decision_time", "fundamental_cutoff_time", "created_at", "news_cutoff_time", "realtime_snapshot_time")
    @classmethod
    def timezone_required(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("temporal decision timestamps must be timezone-aware")
        return value


class DatasetWatermark(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_name: str
    provider: str = "tushare"
    requested_trade_date: date | None = None
    latest_trade_date: date | None = None
    latest_period: str | None = None
    latest_available_at: datetime | None = None
    fetched_at: datetime
    row_count: int = Field(ge=0)
    expected_count: int = Field(ge=0)
    excluded_count: int = Field(default=0, ge=0)
    raw_actual_count: int = Field(default=0, ge=0)
    unique_stock_count: int = Field(default=0, ge=0)
    expected_stock_count: int = Field(default=0, ge=0)
    duplicate_count: int = Field(default=0, ge=0)
    unexpected_stock_count: int = Field(default=0, ge=0)
    excluded_stock_count: int = Field(default=0, ge=0)
    exclusion_reason_counts: dict[str, int] = Field(default_factory=dict)
    coverage_ratio: float = Field(ge=0, le=1)
    is_complete: bool
    is_stale: bool
    schema_version: str
    cache_key: str | None = None
    source_status: str
    error_category: str | None = None
    factor_available_at: datetime | None = None
    adjustment_mode: str | None = None
    adjusted_price_series_version: str | None = None


class RunDataManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    run_id: str
    run_mode: RunMode
    decision_time: datetime
    base_market_trade_date: date | None = None
    target_trade_date: date | None = None
    news_cutoff_time: datetime | None = None
    fundamental_cutoff_time: datetime
    required_dataset_watermarks: list[DatasetWatermark]
    optional_dataset_watermarks: list[DatasetWatermark] = Field(default_factory=list)
    temporal_status: TemporalStatus
    actionable: bool
    block_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime

    def watermark(self, name: str) -> DatasetWatermark | None:
        return next((item for item in self.required_dataset_watermarks + self.optional_dataset_watermarks if item.dataset_name == name), None)
