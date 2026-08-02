from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, JSON, Numeric, String, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.models.mixins import IDMixin, ReprMixin, TimestampMixin


VALUE = Numeric(20, 10)
PRICE = Numeric(18, 6)


class FullUniverseQuantSnapshot(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "full_universe_quant_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "quant_run_id",
            "evaluation_version",
            "evaluation_scope",
            name="uq_full_universe_snapshot_business",
        ),
        Index(
            "ix_full_universe_snapshot_date_version",
            "ranking_trade_date",
            "quant_factor_version",
        ),
    )

    snapshot_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    evaluation_version: Mapped[str] = mapped_column(String(96), nullable=False)
    evaluation_scope: Mapped[str] = mapped_column(String(64), nullable=False)
    quant_factor_version: Mapped[str] = mapped_column(String(96), nullable=False)
    quant_run_id: Mapped[str] = mapped_column(String(128), nullable=False)
    ranking_trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    decision_as_of_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_universe_count: Mapped[int | None] = mapped_column()
    scored_universe_count: Mapped[int] = mapped_column(nullable=False)
    eligible_universe_count: Mapped[int] = mapped_column(nullable=False)
    excluded_universe_count: Mapped[int] = mapped_column(nullable=False)
    return_basis: Mapped[str] = mapped_column(String(64), nullable=False)
    execution_contract_version: Mapped[str] = mapped_column(String(96), nullable=False)
    production_or_shadow: Mapped[str] = mapped_column(String(16), nullable=False)
    source_quant_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    universe_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    data_status: Mapped[str] = mapped_column(String(32), nullable=False)
    industry_mapping_status: Mapped[str] = mapped_column(String(48), nullable=False)
    industry_mapping_hash: Mapped[str | None] = mapped_column(String(64))
    market_regime: Mapped[str | None] = mapped_column(String(32))
    source_artifacts_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class FullUniverseQuantItem(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "full_universe_quant_item"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "original_rank", name="uq_full_universe_item_rank"),
        UniqueConstraint("snapshot_id", "stock_code", name="uq_full_universe_item_stock"),
        Index("ix_full_universe_item_rank", "snapshot_id", "original_rank"),
        Index("ix_full_universe_item_groups", "snapshot_id", "decile_group", "fixed_band", "head_band"),
    )

    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("full_universe_quant_snapshot.id"), nullable=False
    )
    quant_run_id: Mapped[str] = mapped_column(String(128), nullable=False)
    ranking_trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    stock_name: Mapped[str] = mapped_column(String(128), nullable=False)
    original_rank: Mapped[int] = mapped_column(nullable=False)
    total_score: Mapped[Decimal] = mapped_column(VALUE, nullable=False)
    technical_score: Mapped[Decimal | None] = mapped_column(VALUE)
    capital_score: Mapped[Decimal | None] = mapped_column(VALUE)
    emotion_score: Mapped[Decimal | None] = mapped_column(VALUE)
    momentum_score: Mapped[Decimal | None] = mapped_column(VALUE)
    risk_score: Mapped[Decimal | None] = mapped_column(VALUE)
    quant_factor_version: Mapped[str] = mapped_column(String(96), nullable=False)
    eligible_flag: Mapped[bool] = mapped_column(Boolean, nullable=False)
    exclusion_reason: Mapped[str | None] = mapped_column(Text)
    industry_code: Mapped[str | None] = mapped_column(String(64))
    industry_name: Mapped[str | None] = mapped_column(String(128))
    market_cap: Mapped[Decimal | None] = mapped_column(VALUE)
    float_market_cap: Mapped[Decimal | None] = mapped_column(VALUE)
    amount: Mapped[Decimal | None] = mapped_column(VALUE)
    turnover_rate: Mapped[Decimal | None] = mapped_column(VALUE)
    baseline_close: Mapped[Decimal | None] = mapped_column(PRICE)
    baseline_trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    data_status: Mapped[str] = mapped_column(String(32), nullable=False)
    source_row_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    decile_group: Mapped[str] = mapped_column(String(16), nullable=False)
    fixed_band: Mapped[str] = mapped_column(String(32), nullable=False)
    head_band: Mapped[str] = mapped_column(String(32), nullable=False)


class FullUniverseEvaluationRun(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "full_universe_evaluation_run"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_full_universe_evaluation_run"),
        Index(
            "ix_full_universe_run_version_date",
            "evaluation_version",
            "quant_factor_version",
            "as_of_date",
        ),
    )

    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluation_version: Mapped[str] = mapped_column(String(96), nullable=False)
    evaluation_scope: Mapped[str] = mapped_column(String(64), nullable=False)
    quant_factor_version: Mapped[str] = mapped_column(String(96), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    return_basis: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(48), nullable=False)
    conclusion_status: Mapped[str] = mapped_column(String(48), nullable=False)
    maximum_market_data_date: Mapped[date | None] = mapped_column(Date)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    report_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    summary_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    artifact_paths_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def _immutable(*_args, **_kwargs) -> None:
    raise ValueError("FULL_UNIVERSE_SNAPSHOT_IMMUTABLE_CONFLICT")


for _model in (
    FullUniverseQuantSnapshot,
    FullUniverseQuantItem,
    FullUniverseEvaluationRun,
):
    event.listen(_model, "before_update", _immutable)
    event.listen(_model, "before_delete", _immutable)
