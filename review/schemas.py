from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ReviewModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PredictionEvaluationResult(ReviewModel):
    prediction_record_id: int
    stock_code: str
    evaluation_date: date
    expected_direction: str | None = None
    actual_direction: str | None = None
    expected_return: Decimal | None = None
    actual_return: Decimal | None = None
    actual_max_drawdown: Decimal | None = None
    is_correct: bool | None = None
    error_reason: str | None = None


class OrderPlanEvaluationResult(ReviewModel):
    order_plan_id: int
    stock_code: str
    evaluation_date: date
    recommended_price: Decimal | None = None
    actual_open: Decimal | None = None
    actual_high: Decimal | None = None
    actual_low: Decimal | None = None
    actual_close: Decimal | None = None
    was_filled: bool = False
    simulated_fill_price: Decimal | None = None
    best_possible_price: Decimal | None = None
    worst_possible_price: Decimal | None = None
    price_quality_score: Decimal = Decimal("0")
    missed_opportunity: bool = False
    risk_avoided: bool = False
    evaluation_reason: str


class PortfolioEvaluationResult(ReviewModel):
    account_id: int | None = None
    account_type: str
    initial_cash: Decimal
    cash: Decimal
    total_asset: Decimal
    market_value: Decimal = Decimal("0")
    profit_loss: Decimal
    profit_loss_percent: Decimal
    max_drawdown: Decimal
    win_rate: Decimal
    order_count: int
    filled_order_count: int
    order_fill_rate: Decimal


class ComparisonResult(ReviewModel):
    ai_account_id: int | None = None
    human_account_id: int | None = None
    human_summary: str
    ai_better_on_profit: bool | None = None
    ai_profit_loss_percent: Decimal | None = None
    human_profit_loss_percent: Decimal | None = None
    ai_win_rate: Decimal | None = None
    human_win_rate: Decimal | None = None
    ai_max_drawdown: Decimal | None = None
    human_max_drawdown: Decimal | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class DailyReviewResult(ReviewModel):
    date: date
    account_id: int | None = None
    market_summary: str
    ai_summary: str
    human_summary: str
    prediction_accuracy: Decimal
    order_price_quality: Decimal
    profit_loss: Decimal
    max_drawdown: Decimal
    win_rate: Decimal
    mistake_analysis: str
    suggestion: str
    final_review_score: Decimal
    module_scores: dict[str, Decimal]
    created_at: datetime
