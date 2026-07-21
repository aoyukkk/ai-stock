from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, Index, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.models.mixins import IDMixin, TimestampMixin


class IFindShadowAcceptanceRun(IDMixin, TimestampMixin, Base):
    __tablename__ = "ifind_shadow_acceptance_run"
    __table_args__ = (Index("ix_ifind_acceptance_mode_status", "acceptance_mode", "status"),)

    acceptance_run_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    acceptance_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trade_date: Mapped[date | None] = mapped_column(Date)
    market_session: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(48), nullable=False)
    integration_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    index_requested: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    index_returned: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stock_requested: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stock_returned: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    minute_stock_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    external_call_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    auth_call_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cache_hit_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    database_insert_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    index_coverage_ratio: Mapped[float | None] = mapped_column(Float)
    stock_coverage_ratio: Mapped[float | None] = mapped_column(Float)
    minute_completeness_ratio: Mapped[float | None] = mapped_column(Float)
    realtime_delay_p50: Mapped[float | None] = mapped_column(Float)
    realtime_delay_p95: Mapped[float | None] = mapped_column(Float)
    maximum_delay: Mapped[float | None] = mapped_column(Float)
    provider_timestamp_ratio: Mapped[float | None] = mapped_column(Float)
    dual_source_match_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    material_conflict_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    time_semantics_status: Mapped[str] = mapped_column(String(32), nullable=False)
    business_immutability_passed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    business_immutability_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    pool_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    config_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    dual_source_summary_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    report_path: Mapped[str | None] = mapped_column(String(1024))


class IFindShadowAcceptanceItem(IDMixin, TimestampMixin, Base):
    __tablename__ = "ifind_shadow_acceptance_item"
    __table_args__ = (Index("ix_ifind_acceptance_item_run_capability", "acceptance_run_id", "capability"),)

    acceptance_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    capability: Mapped[str] = mapped_column(String(64), nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    round_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    provider_time: Mapped[str | None] = mapped_column(String(64))
    observation_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delay_seconds: Mapped[float | None] = mapped_column(Float)
    coverage_status: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_status: Mapped[str] = mapped_column(String(32), nullable=False)
    freshness_status: Mapped[str] = mapped_column(String(32), nullable=False)
    comparison_status: Mapped[str | None] = mapped_column(String(32))
    error_category: Mapped[str | None] = mapped_column(String(64))
    response_hash: Mapped[str | None] = mapped_column(String(64))
