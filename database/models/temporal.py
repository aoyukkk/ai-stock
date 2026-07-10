from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Index, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.models.mixins import IDMixin, ReprMixin, TimestampMixin


class RunDataManifestRecord(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "run_data_manifest"
    __table_args__ = (Index("ix_run_data_manifest_run_id", "run_id", unique=True),)
    manifest_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    run_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    decision_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    base_market_trade_date: Mapped[date | None] = mapped_column(Date)
    target_trade_date: Mapped[date | None] = mapped_column(Date)
    news_cutoff_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fundamental_cutoff_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    required_dataset_watermarks: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    optional_dataset_watermarks: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    temporal_status: Mapped[str] = mapped_column(String(32), nullable=False)
    actionable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    block_reasons: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    warnings: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    request_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
