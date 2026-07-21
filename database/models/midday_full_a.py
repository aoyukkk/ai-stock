from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.models.mixins import IDMixin, ReprMixin, TimestampMixin


class MiddayFullARadarRun(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "midday_full_a_radar_run"
    __table_args__ = (
        Index("ix_midday_full_a_trade_date", "trade_date", "status"),
        UniqueConstraint("input_hash", name="uq_midday_full_a_input_hash"),
        UniqueConstraint("run_id", name="uq_midday_full_a_run_id"),
    )
    run_id: Mapped[str] = mapped_column(String(80), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    cutoff_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    run_mode: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(String(48), nullable=False)
    stage: Mapped[str] = mapped_column(String(48), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    universe_hash: Mapped[str | None] = mapped_column(String(64))
    asof_data_hash: Mapped[str | None] = mapped_column(String(64))
    baseline_quant_hash: Mapped[str | None] = mapped_column(String(64))
    radar_config_hash: Mapped[str | None] = mapped_column(String(64))
    radar_output_hash: Mapped[str | None] = mapped_column(String(64))
    regime_hash: Mapped[str | None] = mapped_column(String(64))
    rule_output_hash: Mapped[str | None] = mapped_column(String(64))
    llm_output_hash: Mapped[str | None] = mapped_column(String(64))
    export_hash: Mapped[str | None] = mapped_column(String(64))
    current_git_head: Mapped[str | None] = mapped_column(String(64))
    counts_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    audit_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    report_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    output_paths_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(96))
    error_message: Mapped[str | None] = mapped_column(Text)
    execution_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    execution_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    real_orders: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    virtual_orders: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    scheduler_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class MiddayFullARadarResult(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "midday_full_a_radar_result"
    __table_args__ = (
        UniqueConstraint("run_id", "stock_code", name="uq_midday_full_a_run_stock"),
        Index("ix_midday_full_a_result_rank", "run_id", "midday_rank"),
    )
    run_id: Mapped[str] = mapped_column(String(80), nullable=False)
    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    stock_name: Mapped[str | None] = mapped_column(String(128))
    industry: Mapped[str | None] = mapped_column(String(128))
    midday_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    industry_rank: Mapped[int | None] = mapped_column(Integer)
    baseline_quant_score: Mapped[float] = mapped_column(Float, nullable=False)
    morning_relative_strength: Mapped[float | None] = mapped_column(Float)
    morning_volume_price: Mapped[float | None] = mapped_column(Float)
    sector_resonance: Mapped[float | None] = mapped_column(Float)
    opening_risk_quality: Mapped[float | None] = mapped_column(Float)
    midday_radar_score: Mapped[float] = mapped_column(Float, nullable=False)
    data_quality: Mapped[str] = mapped_column(String(32), nullable=False)
    score_version: Mapped[str] = mapped_column(String(64), nullable=False)
    risk_flags_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
