from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.models.mixins import IDMixin, ReprMixin, TimestampMixin


class IndexMarketDailyShadow(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "index_market_daily"
    __table_args__ = (UniqueConstraint("index_code", "trade_date", "provider", name="uq_index_market_daily_provider"),)
    index_code: Mapped[str] = mapped_column(String(32), nullable=False)
    index_name: Mapped[str | None] = mapped_column(String(128))
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float | None] = mapped_column(Float)
    pre_close: Mapped[float | None] = mapped_column(Float)
    change_percent: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    amount: Mapped[float | None] = mapped_column(Float)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    transport: Mapped[str] = mapped_column(String(16), nullable=False)
    data_status: Mapped[str] = mapped_column(String(32), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provider_time: Mapped[str | None] = mapped_column(String(64))
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)


class MarketSnapshotShadow(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "market_snapshot"
    __table_args__ = (UniqueConstraint("stock_code", "snapshot_time", "provider", name="uq_market_snapshot_provider_time"),
                      Index("ix_market_snapshot_provider_status", "provider", "data_status"))
    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    stock_name: Mapped[str | None] = mapped_column(String(128))
    snapshot_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provider_time: Mapped[str | None] = mapped_column(String(64))
    latest: Mapped[float | None] = mapped_column(Float)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    pre_close: Mapped[float | None] = mapped_column(Float)
    change_percent: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    amount: Mapped[float | None] = mapped_column(Float)
    limit_up: Mapped[float | None] = mapped_column(Float)
    limit_down: Mapped[float | None] = mapped_column(Float)
    observed_delay_seconds: Mapped[float | None] = mapped_column(Float)
    market_session: Mapped[str | None] = mapped_column(String(32))
    data_status: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    transport: Mapped[str] = mapped_column(String(16), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    response_hash: Mapped[str | None] = mapped_column(String(64))
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)


class MarketMinuteBarShadow(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "market_minute_bar"
    __table_args__ = (UniqueConstraint("stock_code", "bar_time", "interval", "provider", name="uq_market_minute_provider_bar"),)
    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    bar_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    interval: Mapped[str] = mapped_column(String(16), nullable=False)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    amount: Mapped[float | None] = mapped_column(Float)
    change_percent: Mapped[float | None] = mapped_column(Float)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    data_status: Mapped[str] = mapped_column(String(32), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)


class ExternalProviderUsage(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "external_provider_usage"
    __table_args__ = (Index("ix_external_provider_usage_time", "provider", "requested_at"),)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    transport: Mapped[str] = mapped_column(String(16), nullable=False)
    capability: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    code_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    indicator_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    requested_rows_estimate: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    returned_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_category: Mapped[str | None] = mapped_column(String(64))
    job_id: Mapped[str | None] = mapped_column(String(128))
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
