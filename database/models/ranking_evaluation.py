from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, JSON, Numeric, String, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.models.mixins import IDMixin, ReprMixin, TimestampMixin


PRICE = Numeric(18, 6)
VALUE = Numeric(20, 10)


class RankingEvaluationState(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "ranking_evaluation_state"
    __table_args__ = (UniqueConstraint("evaluation_version", name="uq_ranking_evaluation_state_version"),)

    evaluation_version: Mapped[str] = mapped_column(String(64), nullable=False)
    activation_date: Mapped[date] = mapped_column(Date, nullable=False)
    activated_by_snapshot_id: Mapped[str] = mapped_column(String(64), nullable=False)
    configuration_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class RankingEvaluationSnapshot(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "ranking_evaluation_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "ranking_trade_date",
            "factor_version",
            "evaluation_scope",
            name="uq_ranking_eval_date_factor_scope",
        ),
        Index("ix_ranking_eval_snapshot_source", "source_quant_run_id"),
        Index("ix_ranking_eval_snapshot_date_version", "ranking_trade_date", "factor_version"),
    )

    evaluation_version: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    snapshot_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    ranking_trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_quant_run_id: Mapped[str] = mapped_column(String(128), nullable=False)
    model_name: Mapped[str] = mapped_column(String(96), nullable=False)
    score_version: Mapped[str] = mapped_column(String(96), nullable=False)
    factor_version: Mapped[str] = mapped_column(String(96), nullable=False)
    production_or_shadow: Mapped[str] = mapped_column(String(16), nullable=False)
    price_basis: Mapped[str] = mapped_column(String(32), nullable=False)
    return_basis: Mapped[str] = mapped_column(String(32), nullable=False)
    top_n: Mapped[int] = mapped_column(nullable=False)
    source_input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluation_scope: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_origin: Mapped[str] = mapped_column(String(32), nullable=False)
    overall_data_status: Mapped[str] = mapped_column(String(16), nullable=False)
    raw_artifacts_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class RankingEvaluationSnapshotItem(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "ranking_evaluation_snapshot_item"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "source_row_number", name="uq_ranking_eval_snapshot_row"),
        Index("ix_ranking_eval_snapshot_rank", "snapshot_id", "original_rank"),
        Index("ix_ranking_eval_snapshot_stock", "snapshot_id", "stock_code"),
    )

    snapshot_id: Mapped[int] = mapped_column(ForeignKey("ranking_evaluation_snapshot.id"), nullable=False)
    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    ts_code: Mapped[str] = mapped_column(String(32), nullable=False)
    stock_name: Mapped[str] = mapped_column(String(128), nullable=False)
    original_rank: Mapped[int] = mapped_column(nullable=False)
    quant_score: Mapped[Decimal | None] = mapped_column(VALUE)
    baseline_trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    baseline_close: Mapped[Decimal | None] = mapped_column(PRICE)
    baseline_price_source: Mapped[str] = mapped_column(String(64), nullable=False)
    baseline_source_hash: Mapped[str | None] = mapped_column(String(64))
    original_group: Mapped[str] = mapped_column(String(16), nullable=False)
    source_row_number: Mapped[int] = mapped_column(nullable=False)
    row_data_status: Mapped[str] = mapped_column(String(16), nullable=False)
    row_issue_code: Mapped[str | None] = mapped_column(String(64))
    row_issue_detail: Mapped[str | None] = mapped_column(Text)


class RankingEvaluationForwardOutcome(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "ranking_evaluation_forward_outcome"
    __table_args__ = (
        UniqueConstraint("snapshot_item_id", "horizon", name="uq_ranking_eval_item_horizon"),
        Index("ix_ranking_eval_outcome_due_status", "due_trade_date", "outcome_status"),
    )

    snapshot_item_id: Mapped[int] = mapped_column(
        ForeignKey("ranking_evaluation_snapshot_item.id"), nullable=False
    )
    horizon: Mapped[int] = mapped_column(nullable=False)
    due_trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    baseline_close: Mapped[Decimal | None] = mapped_column(PRICE)
    future_close: Mapped[Decimal | None] = mapped_column(PRICE)
    return_decimal: Mapped[Decimal | None] = mapped_column(VALUE)
    return_percent: Mapped[Decimal | None] = mapped_column(VALUE)
    adjusted_return_decimal: Mapped[Decimal | None] = mapped_column(VALUE)
    adjusted_return_percent: Mapped[Decimal | None] = mapped_column(VALUE)
    baseline_adjustment_factor: Mapped[Decimal | None] = mapped_column(VALUE)
    future_adjustment_factor: Mapped[Decimal | None] = mapped_column(VALUE)
    baseline_price_source: Mapped[str] = mapped_column(String(64), nullable=False)
    future_price_source: Mapped[str | None] = mapped_column(String(64))
    source_data_hash: Mapped[str | None] = mapped_column(String(64))
    outcome_status: Mapped[str] = mapped_column(String(40), nullable=False)
    missing_reason: Mapped[str | None] = mapped_column(Text)
    corporate_action_flag: Mapped[str] = mapped_column(String(32), nullable=False)
    calculated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_as_of_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RankingEvaluationDailyMetric(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "ranking_evaluation_daily_metric"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "horizon", "return_basis", name="uq_ranking_eval_daily_metric"),
        Index("ix_ranking_eval_metric_factor_date", "factor_version", "ranking_trade_date"),
    )

    snapshot_id: Mapped[int] = mapped_column(ForeignKey("ranking_evaluation_snapshot.id"), nullable=False)
    evaluation_version: Mapped[str] = mapped_column(String(64), nullable=False)
    factor_version: Mapped[str] = mapped_column(String(96), nullable=False)
    ranking_trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    horizon: Mapped[int] = mapped_column(nullable=False)
    return_basis: Mapped[str] = mapped_column(String(32), nullable=False)
    valid_sample_count: Mapped[int] = mapped_column(nullable=False)
    missing_sample_count: Mapped[int] = mapped_column(nullable=False)
    coverage_ratio: Mapped[Decimal] = mapped_column(VALUE, nullable=False)
    rank_ic: Mapped[Decimal | None] = mapped_column(VALUE)
    calculation_status: Mapped[str] = mapped_column(String(32), nullable=False)
    top20_mean_return: Mapped[Decimal | None] = mapped_column(VALUE)
    bottom20_mean_return: Mapped[Decimal | None] = mapped_column(VALUE)
    spread: Mapped[Decimal | None] = mapped_column(VALUE)
    top20_valid_count: Mapped[int] = mapped_column(nullable=False)
    bottom20_valid_count: Mapped[int] = mapped_column(nullable=False)
    top20_coverage_ratio: Mapped[Decimal] = mapped_column(VALUE, nullable=False)
    bottom20_coverage_ratio: Mapped[Decimal] = mapped_column(VALUE, nullable=False)
    group_returns_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    group_valid_counts_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    group_coverage_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    adjacent_spreads_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    monotonicity_pass_count: Mapped[int | None] = mapped_column()
    monotonicity_label: Mapped[str] = mapped_column(String(32), nullable=False)
    metric_input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RankingEvaluationWeeklyRun(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "ranking_evaluation_weekly_run"
    __table_args__ = (
        UniqueConstraint(
            "factor_version",
            "week_ending",
            "evaluation_version",
            name="uq_ranking_eval_week_version",
        ),
        Index("ix_ranking_eval_week_status", "week_ending", "status"),
    )

    run_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    factor_version: Mapped[str] = mapped_column(String(96), nullable=False)
    week_ending: Mapped[date] = mapped_column(Date, nullable=False)
    evaluation_version: Mapped[str] = mapped_column(String(64), nullable=False)
    activation_date: Mapped[date] = mapped_column(Date, nullable=False)
    as_of_trade_date: Mapped[date | None] = mapped_column(Date)
    return_basis: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    data_status: Mapped[str] = mapped_column(String(16), nullable=False)
    summary_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    report_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_paths_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RankingEvaluationDataIssue(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "ranking_evaluation_data_issue"
    __table_args__ = (
        UniqueConstraint("issue_hash", name="uq_ranking_eval_issue_hash"),
        Index("ix_ranking_eval_issue_version_date", "affected_version", "affected_date"),
    )

    issue_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    issue_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("ranking_evaluation_snapshot.id"))
    weekly_run_id: Mapped[int | None] = mapped_column(ForeignKey("ranking_evaluation_weekly_run.id"))
    issue_code: Mapped[str] = mapped_column(String(64), nullable=False)
    issue_level: Mapped[str] = mapped_column(String(16), nullable=False)
    affected_date: Mapped[date | None] = mapped_column(Date)
    affected_stock: Mapped[str | None] = mapped_column(String(32))
    affected_version: Mapped[str | None] = mapped_column(String(96))
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RankingEvaluationArtifact(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "ranking_evaluation_artifact"
    __table_args__ = (
        UniqueConstraint("artifact_path", name="uq_ranking_eval_artifact_path"),
        Index("ix_ranking_eval_artifact_run", "weekly_run_id"),
    )

    artifact_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    weekly_run_id: Mapped[int] = mapped_column(ForeignKey("ranking_evaluation_weekly_run.id"), nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(32), nullable=False)
    artifact_path: Mapped[str] = mapped_column(Text, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    style_hash: Mapped[str | None] = mapped_column(String(64))
    immutable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ModelEffectivenessStageSnapshot(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "model_effectiveness_stage_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "screening_run_id",
            "stage_type",
            "screening_version",
            name="uq_model_effectiveness_business_stage",
        ),
        Index(
            "ix_model_effectiveness_stage_version_date",
            "ranking_trade_date",
            "quant_factor_version",
            "screening_version",
        ),
    )

    stage_snapshot_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    cohort_snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("ranking_evaluation_snapshot.id"), nullable=False
    )
    evaluation_version: Mapped[str] = mapped_column(String(64), nullable=False)
    stage_type: Mapped[str] = mapped_column(String(32), nullable=False)
    ranking_trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    source_quant_run_id: Mapped[str] = mapped_column(String(128), nullable=False)
    screening_run_id: Mapped[str] = mapped_column(String(128), nullable=False)
    quant_factor_version: Mapped[str] = mapped_column(String(96), nullable=False)
    screening_version: Mapped[str] = mapped_column(String(96), nullable=False)
    event_review_version: Mapped[str | None] = mapped_column(String(96))
    risk_version: Mapped[str | None] = mapped_column(String(96))
    prompt_version: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_schema_version: Mapped[str] = mapped_column(String(96), nullable=False)
    checkpoint_contract_version: Mapped[str] = mapped_column(String(96), nullable=False)
    production_or_shadow: Mapped[str] = mapped_column(String(16), nullable=False)
    return_basis: Mapped[str] = mapped_column(String(40), nullable=False)
    execution_contract_version: Mapped[str] = mapped_column(String(96), nullable=False)
    quant_top100_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    flash_input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    flash_input_count: Mapped[int] = mapped_column(nullable=False)
    decision_as_of_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    output_available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_market_open_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actionability_status: Mapped[str] = mapped_column(String(40), nullable=False)
    cohort_match: Mapped[bool] = mapped_column(Boolean, nullable=False)
    run_status: Mapped[str] = mapped_column(String(32), nullable=False)
    reliability_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_artifacts_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class ModelEffectivenessStageItem(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "model_effectiveness_stage_item"
    __table_args__ = (
        UniqueConstraint(
            "stage_snapshot_id", "source_row_number",
            name="uq_model_effectiveness_stage_row",
        ),
        Index("ix_model_effectiveness_stage_item_stock", "stage_snapshot_id", "stock_code"),
        Index("ix_model_effectiveness_stage_item_rank", "stage_snapshot_id", "flash_rank"),
    )

    stage_snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("model_effectiveness_stage_snapshot.id"), nullable=False
    )
    cohort_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("ranking_evaluation_snapshot_item.id")
    )
    source_row_number: Mapped[int] = mapped_column(nullable=False)
    stock_code: Mapped[str] = mapped_column(String(32), nullable=False)
    stock_name: Mapped[str] = mapped_column(String(128), nullable=False)
    original_quant_rank: Mapped[int | None] = mapped_column()
    quant_score: Mapped[Decimal | None] = mapped_column(VALUE)
    flash_input_member: Mapped[bool] = mapped_column(Boolean, nullable=False)
    nonstandard_extra: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    flash_score: Mapped[Decimal | None] = mapped_column(VALUE)
    flash_rank: Mapped[int | None] = mapped_column()
    selected_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    final_selected_rank: Mapped[int | None] = mapped_column()
    screening_action: Mapped[str | None] = mapped_column(String(64))
    recommendation: Mapped[str | None] = mapped_column(String(64))
    confidence: Mapped[Decimal | None] = mapped_column(VALUE)
    risk_level: Mapped[str | None] = mapped_column(String(32))
    risk_action: Mapped[str | None] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(Text)
    schema_status: Mapped[str] = mapped_column(String(32), nullable=False)
    task_status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_count: Mapped[int] = mapped_column(nullable=False, default=0)
    repair_count: Mapped[int] = mapped_column(nullable=False, default=0)
    checkpoint_reused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    model_provider: Mapped[str | None] = mapped_column(String(64))
    model_alias: Mapped[str | None] = mapped_column(String(64))
    resolved_model_name: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_schema_version: Mapped[str] = mapped_column(String(96), nullable=False)
    input_hash: Mapped[str | None] = mapped_column(String(64))
    context_hash: Mapped[str | None] = mapped_column(String(64))
    checkpoint_contract_hash: Mapped[str | None] = mapped_column(String(64))
    output_available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_market_open_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actionable_before_next_open: Mapped[bool] = mapped_column(Boolean, nullable=False)
    event_opportunity_score: Mapped[Decimal | None] = mapped_column(VALUE)
    event_evidence_confidence: Mapped[Decimal | None] = mapped_column(VALUE)
    event_action: Mapped[str | None] = mapped_column(String(32))
    direct_search_used: Mapped[bool | None] = mapped_column(Boolean)
    evidence_source: Mapped[str | None] = mapped_column(String(64))
    search_status: Mapped[str | None] = mapped_column(String(64))
    event_evidence_snapshot_hash: Mapped[str | None] = mapped_column(String(64))
    data_status: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_payload_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class ModelEffectivenessDailyMetric(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "model_effectiveness_daily_metric"
    __table_args__ = (
        UniqueConstraint(
            "stage_snapshot_id", "horizon", "return_basis", "actionable_only",
            name="uq_model_effectiveness_daily_metric",
        ),
        Index(
            "ix_model_effectiveness_metric_version_date",
            "screening_version", "ranking_trade_date",
        ),
    )

    stage_snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("model_effectiveness_stage_snapshot.id"), nullable=False
    )
    evaluation_version: Mapped[str] = mapped_column(String(64), nullable=False)
    stage_type: Mapped[str] = mapped_column(String(32), nullable=False)
    quant_factor_version: Mapped[str] = mapped_column(String(96), nullable=False)
    screening_version: Mapped[str] = mapped_column(String(96), nullable=False)
    ranking_trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    horizon: Mapped[int] = mapped_column(nullable=False)
    return_basis: Mapped[str] = mapped_column(String(40), nullable=False)
    actionable_only: Mapped[bool] = mapped_column(Boolean, nullable=False)
    calculation_status: Mapped[str] = mapped_column(String(32), nullable=False)
    valid_sample_count: Mapped[int] = mapped_column(nullable=False)
    metric_payload_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    metric_input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ModelEffectivenessWeeklyRun(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "model_effectiveness_weekly_run"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_model_effectiveness_weekly_run"),
        Index(
            "ix_model_effectiveness_week_version",
            "week_ending", "quant_factor_version", "screening_version",
        ),
    )

    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluation_version: Mapped[str] = mapped_column(String(64), nullable=False)
    quant_factor_version: Mapped[str] = mapped_column(String(96), nullable=False)
    screening_version: Mapped[str] = mapped_column(String(96), nullable=False)
    week_ending: Mapped[date] = mapped_column(Date, nullable=False)
    return_basis: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    data_status: Mapped[str] = mapped_column(String(32), nullable=False)
    summary_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    report_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_paths_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ModelEffectivenessDataIssue(IDMixin, TimestampMixin, ReprMixin, Base):
    __tablename__ = "model_effectiveness_data_issue"
    __table_args__ = (
        UniqueConstraint("issue_hash", name="uq_model_effectiveness_issue"),
        Index(
            "ix_model_effectiveness_issue_version_date",
            "screening_version", "affected_date",
        ),
    )

    issue_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    issue_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    stage_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_effectiveness_stage_snapshot.id")
    )
    issue_code: Mapped[str] = mapped_column(String(64), nullable=False)
    issue_level: Mapped[str] = mapped_column(String(32), nullable=False)
    affected_date: Mapped[date | None] = mapped_column(Date)
    affected_stock: Mapped[str | None] = mapped_column(String(32))
    quant_factor_version: Mapped[str | None] = mapped_column(String(96))
    screening_version: Mapped[str | None] = mapped_column(String(96))
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def _immutable(*_args, **_kwargs) -> None:
    raise ValueError("IMMUTABLE_RANKING_EVALUATION_RECORD")


for _model in (
    RankingEvaluationState,
    RankingEvaluationSnapshot,
    RankingEvaluationSnapshotItem,
    RankingEvaluationWeeklyRun,
    RankingEvaluationArtifact,
    ModelEffectivenessStageSnapshot,
    ModelEffectivenessStageItem,
    ModelEffectivenessWeeklyRun,
):
    event.listen(_model, "before_update", _immutable)
    event.listen(_model, "before_delete", _immutable)
