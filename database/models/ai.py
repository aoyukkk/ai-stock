from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.models.mixins import IDMixin, ReprMixin, TimestampMixin


SCORE = Numeric(8, 4)


class AIAnalysisResult(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "ai_analysis_result"
    __table_args__ = (
        Index("ix_ai_analysis_result_stock_time", "stock_code", "analysis_time"),
        Index("ix_ai_analysis_result_agent_time", "agent_name", "analysis_time"),
    )

    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(128), nullable=False)
    model_name: Mapped[str | None] = mapped_column(String(128))
    score: Mapped[Decimal | None] = mapped_column(SCORE)
    direction: Mapped[str | None] = mapped_column(String(32))
    confidence: Mapped[Decimal | None] = mapped_column(SCORE)
    reason: Mapped[str | None] = mapped_column(Text)
    risk_note: Mapped[str | None] = mapped_column(Text)
    analysis_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    model_version: Mapped[str | None] = mapped_column(String(64))
    llm_usage_id: Mapped[int | None] = mapped_column(ForeignKey("llm_usage.id"))


class StockAIScore(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "stock_ai_score"
    __table_args__ = (
        Index("ix_stock_ai_score_time_final", "time", "final_score"),
        Index("ix_stock_ai_score_stock_time", "stock_code", "time"),
    )

    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    technical_score: Mapped[Decimal | None] = mapped_column(SCORE)
    news_score: Mapped[Decimal | None] = mapped_column(SCORE)
    capital_score: Mapped[Decimal | None] = mapped_column(SCORE)
    emotion_score: Mapped[Decimal | None] = mapped_column(SCORE)
    overseas_score: Mapped[Decimal | None] = mapped_column(SCORE)
    risk_score: Mapped[Decimal | None] = mapped_column(SCORE)
    final_score: Mapped[Decimal | None] = mapped_column(SCORE)
    recommendation: Mapped[str | None] = mapped_column(String(32))
    risk_level: Mapped[str | None] = mapped_column(String(32))
    confidence: Mapped[Decimal | None] = mapped_column(SCORE)
    controller_reason: Mapped[str | None] = mapped_column(Text)
    decision_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("decision_snapshot.id"))


class PredictionRecord(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "prediction_record"
    __table_args__ = (
        Index("ix_prediction_record_stock_time", "stock_code", "prediction_time"),
        Index("ix_prediction_record_time_recommendation", "prediction_time", "recommendation"),
        Index("ix_prediction_record_valid_until", "valid_until"),
        Index("ix_prediction_record_order_plan", "order_plan_id"),
    )

    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    prediction_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    prediction_horizon_days: Mapped[int | None] = mapped_column(Integer)
    expected_direction: Mapped[str | None] = mapped_column(String(32))
    expected_return: Mapped[Decimal | None] = mapped_column(SCORE)
    expected_risk: Mapped[Decimal | None] = mapped_column(SCORE)
    score: Mapped[Decimal | None] = mapped_column(SCORE)
    confidence: Mapped[Decimal | None] = mapped_column(SCORE)
    recommendation: Mapped[str | None] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(Text)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    model_version: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    factor_version: Mapped[str | None] = mapped_column(String(64))
    data_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("decision_snapshot.id"))
    order_plan_id: Mapped[int | None] = mapped_column(ForeignKey("order_plan.id"))


class DecisionSnapshot(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "decision_snapshot"
    __table_args__ = (
        Index("ix_decision_snapshot_stock_time", "stock_code", "snapshot_time"),
        Index("ix_decision_snapshot_time", "snapshot_time"),
    )

    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    snapshot_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    market_data_json: Mapped[dict | None] = mapped_column(JSON)
    factor_json: Mapped[dict | None] = mapped_column(JSON)
    news_json: Mapped[dict | None] = mapped_column(JSON)
    agent_result_json: Mapped[dict | None] = mapped_column(JSON)
    memory_json: Mapped[dict | None] = mapped_column(JSON)
    order_price_json: Mapped[dict | None] = mapped_column(JSON)
    final_score: Mapped[Decimal | None] = mapped_column(SCORE)
    risk_level: Mapped[str | None] = mapped_column(String(32))
    recommendation: Mapped[str | None] = mapped_column(String(32))
