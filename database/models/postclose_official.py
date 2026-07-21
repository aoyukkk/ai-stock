from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.models.mixins import IDMixin, ReprMixin, TimestampMixin


class PostCloseOfficialRun(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "postclose_official_run"
    __table_args__ = (
        Index("ix_postclose_official_run_id", "run_id", unique=True),
        Index("ix_postclose_official_trade_date", "trade_date", "status"),
    )

    run_id: Mapped[str] = mapped_column(String(80), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    report_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    output_paths_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)
    provider_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    per_stock_api_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    real_orders: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    virtual_orders: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    scheduler_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    production_config_changed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
