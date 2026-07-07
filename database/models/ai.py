from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, Index, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, CreatedAtMixin, UpdatedAtMixin


class AIAnalysisResult(Base, CreatedAtMixin):
    __tablename__ = "ai_analysis_result"
    __table_args__ = (
        Index("ix_ai_analysis_result_stock_code_analysis_time", "stock_code", "analysis_time"),
        Index("ix_ai_analysis_result_agent_name_analysis_time", "agent_name", "analysis_time"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    stock_code: Mapped[str | None] = mapped_column(String(20), index=True)
    agent_name: Mapped[str | None] = mapped_column(String(100), index=True)
    model_name: Mapped[str | None] = mapped_column(String(100))
    score: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    direction: Mapped[str | None] = mapped_column(
        String(20),
        comment="Allowed: BUY, WATCH, NEUTRAL, AVOID",
    )
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    reason: Mapped[str | None] = mapped_column(Text)
    risk_note: Mapped[str | None] = mapped_column(Text)
    analysis_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    prompt_version: Mapped[str | None] = mapped_column(String(50))
    model_version: Mapped[str | None] = mapped_column(String(50))
    llm_usage_id: Mapped[int | None] = mapped_column(BigInteger, index=True)


class StockAIScore(Base, CreatedAtMixin):
    __tablename__ = "stock_ai_score"
    __table_args__ = (
        Index("ix_stock_ai_score_time_final_score", "time", "final_score"),
        Index("ix_stock_ai_score_stock_code_time", "stock_code", "time"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    stock_code: Mapped[str | None] = mapped_column(String(20), index=True)
    time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    technical_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    news_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    capital_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    emotion_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    overseas_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    risk_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    final_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), index=True)
    recommendation: Mapped[str | None] = mapped_column(
        String(30),
        comment="Allowed: STRONG_WATCH, WATCH, NEUTRAL, AVOID, BLOCKED",
    )
    risk_level: Mapped[str | None] = mapped_column(
        String(30),
        comment="Allowed: LOW, MEDIUM, HIGH, BLACK_SWAN",
    )
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    controller_reason: Mapped[str | None] = mapped_column(Text)
    decision_snapshot_id: Mapped[int | None] = mapped_column(BigInteger, index=True)


class PredictionRecord(Base, CreatedAtMixin):
    __tablename__ = "prediction_record"
    __table_args__ = (
        Index("ix_prediction_record_stock_code_prediction_time", "stock_code", "prediction_time"),
        Index("ix_prediction_record_prediction_time_recommendation", "prediction_time", "recommendation"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    stock_code: Mapped[str | None] = mapped_column(String(20), index=True)
    prediction_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    prediction_horizon_days: Mapped[int | None] = mapped_column()
    expected_direction: Mapped[str | None] = mapped_column(
        String(20),
        comment="Allowed: UP, DOWN, SIDEWAYS, UNKNOWN",
    )
    expected_return: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    expected_risk: Mapped[str | None] = mapped_column(String(100))
    score: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    recommendation: Mapped[str | None] = mapped_column(String(30), index=True)
    reason: Mapped[str | None] = mapped_column(Text)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    model_version: Mapped[str | None] = mapped_column(String(50))
    prompt_version: Mapped[str | None] = mapped_column(String(50))
    factor_version: Mapped[str | None] = mapped_column(String(50))
    data_snapshot_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    order_plan_id: Mapped[int | None] = mapped_column(BigInteger, index=True)


class DecisionSnapshot(Base, CreatedAtMixin):
    __tablename__ = "decision_snapshot"
    __table_args__ = (
        Index("ix_decision_snapshot_stock_code_snapshot_time", "stock_code", "snapshot_time"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    stock_code: Mapped[str | None] = mapped_column(String(20), index=True)
    snapshot_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    market_data_json: Mapped[dict | None] = mapped_column(JSON)
    factor_json: Mapped[dict | None] = mapped_column(JSON)
    news_json: Mapped[dict | None] = mapped_column(JSON)
    agent_result_json: Mapped[dict | None] = mapped_column(JSON)
    memory_json: Mapped[dict | None] = mapped_column(JSON)
    order_price_json: Mapped[dict | None] = mapped_column(JSON)
    final_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    risk_level: Mapped[str | None] = mapped_column(String(30))
    recommendation: Mapped[str | None] = mapped_column(String(30))


class ExperimentRun(Base, CreatedAtMixin, UpdatedAtMixin):
    __tablename__ = "experiment_run"
    __table_args__ = (Index("ix_experiment_run_start_date_end_date", "start_date", "end_date"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(100), index=True)
    strategy_version: Mapped[str | None] = mapped_column(String(50))
    model_config_version: Mapped[str | None] = mapped_column(String(50))
    prompt_version: Mapped[str | None] = mapped_column(String(50))
    factor_version: Mapped[str | None] = mapped_column(String(50))
    order_price_version: Mapped[str | None] = mapped_column(String(50))
    start_date: Mapped[date | None] = mapped_column(Date, index=True)
    end_date: Mapped[date | None] = mapped_column(Date, index=True)
    initial_cash: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(
        String(20),
        index=True,
        comment="Allowed: PLANNED, RUNNING, FINISHED, FAILED",
    )


class AgentExecutionLog(Base, CreatedAtMixin):
    __tablename__ = "agent_execution_log"
    __table_args__ = (
        Index("ix_agent_execution_log_agent_name_created_at", "agent_name", "created_at"),
        Index("ix_agent_execution_log_stock_code_created_at", "stock_code", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    agent_name: Mapped[str | None] = mapped_column(String(100), index=True)
    task: Mapped[str | None] = mapped_column(String(200))
    stock_code: Mapped[str | None] = mapped_column(String(20), index=True)
    model: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str | None] = mapped_column(
        String(20),
        comment="Allowed: SUCCESS, FAILED, SKIPPED",
    )
    input_summary: Mapped[str | None] = mapped_column(Text)
    output_summary: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LLMUsage(Base, CreatedAtMixin):
    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    provider: Mapped[str | None] = mapped_column(String(100), index=True)
    model_name: Mapped[str | None] = mapped_column(String(100), index=True)
    agent_name: Mapped[str | None] = mapped_column(String(100), index=True)
    task: Mapped[str | None] = mapped_column(String(200))
    input_tokens: Mapped[int | None] = mapped_column()
    output_tokens: Mapped[int | None] = mapped_column()
    cached_input_tokens: Mapped[int | None] = mapped_column()
    total_tokens: Mapped[int | None] = mapped_column()
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    latency_ms: Mapped[int | None] = mapped_column()
    status: Mapped[str | None] = mapped_column(String(20))
    error_message: Mapped[str | None] = mapped_column(Text)
    request_hash: Mapped[str | None] = mapped_column(String(128), index=True)
