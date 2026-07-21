from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.models.mixins import IDMixin, ReprMixin, TimestampMixin


class IntradayMonitorSession(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "intraday_monitor_session"
    __table_args__ = (Index("ix_monitor_session_trade_status", "trade_date", "status"),)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="DRAFT")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    market_session: Mapped[str] = mapped_column(String(32), nullable=False, default="UNKNOWN")
    pool_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pool_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    stock_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_run_ids_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    config_snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    external_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    alert_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    critical_alert_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class IntradayMonitorPoolVersion(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "intraday_monitor_pool_version"
    __table_args__ = (UniqueConstraint("monitor_session_id", "version", name="uq_monitor_pool_version"),)
    monitor_session_id: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_midday_run_id: Mapped[str | None] = mapped_column(String(64))
    raw_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deduplicated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    added_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    removed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pool_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    confirmed_by: Mapped[str] = mapped_column(String(64), nullable=False, default="LOCAL_TRADER")


class IntradayMonitorItem(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "intraday_monitor_item"
    __table_args__ = (UniqueConstraint("monitor_session_id", "stock_code", name="uq_monitor_item_session_stock"),)
    monitor_session_id: Mapped[int] = mapped_column(Integer, nullable=False)
    pool_version_id: Mapped[int] = mapped_column(Integer, nullable=False)
    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    stock_name_snapshot: Mapped[str | None] = mapped_column(String(128))
    source_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    monitor_profile: Mapped[str] = mapped_column(String(40), nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False)
    plan_id: Mapped[int | None] = mapped_column(Integer)
    position_snapshot_id: Mapped[int | None] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    muted_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IntradayMonitorRule(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "intraday_monitor_rule"
    monitor_item_id: Mapped[int] = mapped_column(Integer, nullable=False)
    rule_type: Mapped[str] = mapped_column(String(64), nullable=False)
    threshold_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    comparison: Mapped[str] = mapped_column(String(16), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    consecutive_hits_required: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    hysteresis_percent: Mapped[float] = mapped_column(Float, nullable=False, default=0.2)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    rule_version: Mapped[str] = mapped_column(String(16), nullable=False, default="1.0")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="SYSTEM")


class IntradayMonitorAlert(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "intraday_monitor_alert"
    __table_args__ = (Index("ix_monitor_alert_session_status", "monitor_session_id", "status"), Index("ix_monitor_alert_dedup", "dedup_key"))
    monitor_session_id: Mapped[int] = mapped_column(Integer, nullable=False)
    monitor_item_id: Mapped[int] = mapped_column(Integer, nullable=False)
    rule_id: Mapped[int] = mapped_column(Integer, nullable=False)
    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    current_value_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    threshold_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    market_snapshot_id: Mapped[int | None] = mapped_column(Integer)
    minute_snapshot_hash: Mapped[str | None] = mapped_column(String(64))
    provider_time: Mapped[str | None] = mapped_column(String(64))
    freshness_status: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="TRIGGERED")
    dedup_key: Mapped[str] = mapped_column(String(256), nullable=False)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    escalated_from_alert_id: Mapped[int | None] = mapped_column(Integer)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by: Mapped[str | None] = mapped_column(String(64))
    resolution: Mapped[str | None] = mapped_column(Text)


class IntradayMonitorAlertAction(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "intraday_monitor_alert_action"
    alert_id: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    operated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    operated_by: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    previous_status: Mapped[str] = mapped_column(String(32), nullable=False)
    new_status: Mapped[str] = mapped_column(String(32), nullable=False)


class IntradayMonitorRefresh(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "intraday_monitor_refresh"
    monitor_session_id: Mapped[int] = mapped_column(Integer, nullable=False)
    refresh_type: Mapped[str] = mapped_column(String(32), nullable=False)
    priority: Mapped[str] = mapped_column(String(8), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requested_codes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    returned_codes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    missing_codes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    external_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_hits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_category: Mapped[str | None] = mapped_column(String(64))
