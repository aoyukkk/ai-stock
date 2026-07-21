from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.models.mixins import IDMixin, ReprMixin, TimestampMixin


class MarketDailySnapshot(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "market_daily_snapshot"
    __table_args__ = (
        UniqueConstraint("trade_date", "snapshot_hash", name="uq_market_snapshot_trade_hash"),
        Index("ix_market_snapshot_trade_date", "trade_date"),
    )

    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    decision_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    market_direction: Mapped[str] = mapped_column(String(16), nullable=False)
    market_regime: Mapped[str] = mapped_column(String(32), nullable=False)
    regime_score: Mapped[float] = mapped_column(Float, nullable=False)
    regime_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    data_quality_score: Mapped[float] = mapped_column(Float, nullable=False)
    index_summary_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    breadth_summary_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    turnover_summary_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    limit_summary_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    industry_summary_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    concept_summary_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    style_summary_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    capital_summary_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    technical_summary_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    metric_ids_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    source_status_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    dataset_watermark_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)


class MarketReviewEvidence(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "market_review_evidence"
    __table_args__ = (
        Index("ix_market_review_evidence_trade_date", "trade_date"),
        Index("ix_market_review_evidence_status", "status"),
    )

    evidence_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    source_name: Mapped[str] = mapped_column(String(128), nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    publish_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    short_summary: Mapped[str] = mapped_column(Text, nullable=False)
    source_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    official: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    affected_scope: Mapped[str] = mapped_column(String(128), nullable=False)
    affected_sectors_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    credibility_score: Mapped[float] = mapped_column(Float, nullable=False)
    relevance_score: Mapped[float] = mapped_column(Float, nullable=False)
    timeliness_score: Mapped[float] = mapped_column(Float, nullable=False)
    final_evidence_score: Mapped[float] = mapped_column(Float, nullable=False)
    duplicate_group_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)


class MarketReviewRun(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "market_review_run"
    __table_args__ = (
        Index("ix_market_review_run_trade_status", "trade_date", "status"),
        Index("ix_market_review_run_input_hash", "review_input_hash"),
    )

    run_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    cache_status: Mapped[str] = mapped_column(String(32), nullable=False, default="SUCCESS")
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("market_daily_snapshot.id"), nullable=False)
    search_status: Mapped[str] = mapped_column(String(16), nullable=False)
    evidence_count: Mapped[int] = mapped_column(default=0, nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    review_input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model_alias: Mapped[str] = mapped_column(String(128), nullable=False)
    actual_model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    contract_version: Mapped[str] = mapped_column(String(64), nullable=False)
    market_direction: Mapped[str] = mapped_column(String(16), nullable=False)
    market_regime: Mapped[str] = mapped_column(String(32), nullable=False)
    market_outlook_score: Mapped[float] = mapped_column(Float, nullable=False)
    base_case_probability: Mapped[int] = mapped_column(nullable=False)
    bull_case_probability: Mapped[int] = mapped_column(nullable=False)
    bear_case_probability: Mapped[int] = mapped_column(nullable=False)
    overall_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    data_conflict: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    review_summary_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    token_usage_id: Mapped[int | None] = mapped_column(ForeignKey("llm_usage.id"))
    pipeline_run_id: Mapped[str | None] = mapped_column(String(128))
    excel_export_status: Mapped[str] = mapped_column(String(32), nullable=False, default="NOT_RUN")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)


class MarketReviewDriver(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "market_review_driver"
    __table_args__ = (Index("ix_market_review_driver_run_rank", "market_review_run_id", "rank"),)

    market_review_run_id: Mapped[int] = mapped_column(ForeignKey("market_review_run.id"), nullable=False)
    driver_type: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    impact_strength: Mapped[int] = mapped_column(nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    affected_scope: Mapped[str] = mapped_column(String(128), nullable=False)
    affected_sectors_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    evidence_ids_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    metric_ids_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    rank: Mapped[int] = mapped_column(nullable=False)


class MarketOutlookScenario(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "market_outlook_scenario"
    __table_args__ = (
        UniqueConstraint("market_review_run_id", "scenario_type", name="uq_market_outlook_run_type"),
    )

    market_review_run_id: Mapped[int] = mapped_column(ForeignKey("market_review_run.id"), nullable=False)
    scenario_type: Mapped[str] = mapped_column(String(16), nullable=False)
    probability: Mapped[int] = mapped_column(nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    supporting_reasons_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    triggers_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    invalidation_conditions_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    watch_items_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
