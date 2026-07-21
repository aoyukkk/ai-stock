from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Index, JSON, Numeric, String, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.models.mixins import IDMixin, ReprMixin, TimestampMixin


SCORE = Numeric(12, 4)


class AdmissionV2Run(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "admission_v2_run"
    __table_args__ = (
        Index("ix_admission_v2_trade_status", "trade_date", "status"),
        Index("ix_admission_v2_input_hash", "input_hash", unique=True),
    )

    run_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    quant_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    v1_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    config_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    candidate_count: Mapped[int] = mapped_column(nullable=False)
    pass_count: Mapped[int] = mapped_column(nullable=False)
    review_count: Mapped[int] = mapped_column(nullable=False)
    block_count: Mapped[int] = mapped_column(nullable=False)
    admitted_count: Mapped[int] = mapped_column(nullable=False)
    manual_challenge_count: Mapped[int] = mapped_column(nullable=False)
    market_emotion_score: Mapped[Decimal | None] = mapped_column(SCORE)
    market_emotion_state: Mapped[str] = mapped_column(String(32), nullable=False)
    market_regime: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy_distribution: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    shadow_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    enabled_in_production: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    quant_hash_before: Mapped[str] = mapped_column(String(64), nullable=False)
    quant_hash_after: Mapped[str] = mapped_column(String(64), nullable=False)
    flash_hash_before: Mapped[str] = mapped_column(String(64), nullable=False)
    flash_hash_after: Mapped[str] = mapped_column(String(64), nullable=False)
    pro_hash_before: Mapped[str] = mapped_column(String(64), nullable=False)
    pro_hash_after: Mapped[str] = mapped_column(String(64), nullable=False)
    llm_call_count: Mapped[int] = mapped_column(nullable=False, default=0)
    external_api_call_count: Mapped[int] = mapped_column(nullable=False, default=0)
    order_creation_count: Mapped[int] = mapped_column(nullable=False, default=0)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StrategyClassificationResult(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "strategy_classification_result"
    __table_args__ = (
        UniqueConstraint("run_id", "stock_code", name="uq_strategy_classification_run_stock"),
        Index("ix_strategy_classification_trade_strategy", "trade_date", "strategy_id"),
    )

    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy_id: Mapped[str] = mapped_column(String(32), nullable=False)
    primary_strategy: Mapped[str] = mapped_column(String(32), nullable=False)
    alternative_strategies_json: Mapped[list] = mapped_column(JSON, nullable=False)
    strategy_fit_score: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    pattern_fit_score: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    regime_compatibility_score: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    sector_compatibility_score: Mapped[Decimal | None] = mapped_column(SCORE)
    data_quality_score: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    confidence: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    matched_conditions_json: Mapped[list] = mapped_column(JSON, nullable=False)
    failed_conditions_json: Mapped[list] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    classifier_version: Mapped[str] = mapped_column(String(64), nullable=False)
    details_json: Mapped[dict] = mapped_column(JSON, nullable=False)


class MarketEmotionSnapshot(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "market_emotion_snapshot"
    __table_args__ = (
        UniqueConstraint("trade_date", "input_hash", "version", name="uq_market_emotion_trade_hash_version"),
        Index("ix_market_emotion_trade_state", "trade_date", "emotion_state"),
    )

    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    decision_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    breadth_health: Mapped[Decimal | None] = mapped_column(SCORE)
    limit_structure_health: Mapped[Decimal | None] = mapped_column(SCORE)
    break_board_health: Mapped[Decimal | None] = mapped_column(SCORE)
    median_return_health: Mapped[Decimal | None] = mapped_column(SCORE)
    turnover_health: Mapped[Decimal | None] = mapped_column(SCORE)
    tail_risk_health: Mapped[Decimal | None] = mapped_column(SCORE)
    market_emotion_score: Mapped[Decimal | None] = mapped_column(SCORE)
    emotion_state: Mapped[str] = mapped_column(String(32), nullable=False)
    market_regime: Mapped[str] = mapped_column(String(32), nullable=False)
    component_coverage: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    missing_components_json: Mapped[list] = mapped_column(JSON, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)


class EntryTimingV2Result(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "entry_timing_v2_result"
    __table_args__ = (
        UniqueConstraint("run_id", "stock_code", "pool_type", name="uq_entry_timing_v2_run_stock_pool"),
        Index("ix_entry_timing_v2_trade_status", "trade_date", "admission_status_v2"),
        Index("ix_entry_timing_v2_strategy_emotion", "strategy_id", "market_emotion_state"),
    )

    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    stock_name: Mapped[str | None] = mapped_column(String(128))
    pool_type: Mapped[str] = mapped_column(String(32), nullable=False)
    selection_source: Mapped[str | None] = mapped_column(String(32))
    quant_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    quant_rank: Mapped[int | None] = mapped_column()
    quant_score: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    risk_score: Mapped[Decimal | None] = mapped_column(SCORE)
    flash_score: Mapped[Decimal | None] = mapped_column(SCORE)
    strategy_id: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy_fit_score: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    strategy_confidence: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    classification_status: Mapped[str] = mapped_column(String(32), nullable=False)
    market_emotion_score: Mapped[Decimal | None] = mapped_column(SCORE)
    market_emotion_state: Mapped[str] = mapped_column(String(32), nullable=False)
    market_regime: Mapped[str] = mapped_column(String(32), nullable=False)
    market_gate_status: Mapped[str] = mapped_column(String(32), nullable=False)
    entry_timing_v1_score: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    entry_timing_v2_score: Mapped[Decimal | None] = mapped_column(SCORE)
    admission_ranking_score_v2: Mapped[Decimal | None] = mapped_column(SCORE)
    admission_status_v1: Mapped[str] = mapped_column(String(32), nullable=False)
    admission_status_v2: Mapped[str] = mapped_column(String(32), nullable=False)
    risk_flags_json: Mapped[list] = mapped_column(JSON, nullable=False)
    block_reasons_json: Mapped[list] = mapped_column(JSON, nullable=False)
    review_reasons_json: Mapped[list] = mapped_column(JSON, nullable=False)
    requires_manual_review: Mapped[bool] = mapped_column(Boolean, nullable=False)
    component_scores_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    data_coverage_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    diagnostics_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)


def _immutable_v2_snapshot(*_args, **_kwargs) -> None:
    raise ValueError("IMMUTABLE_ENTRY_TIMING_V2_SNAPSHOT")


for _model in (AdmissionV2Run, StrategyClassificationResult, MarketEmotionSnapshot, EntryTimingV2Result):
    event.listen(_model, "before_update", _immutable_v2_snapshot)
    event.listen(_model, "before_delete", _immutable_v2_snapshot)
