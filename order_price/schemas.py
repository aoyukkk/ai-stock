from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from datasource.schemas import KlineBar


Side = Literal["BUY", "SELL"]
PriceType = Literal["CONSERVATIVE", "BALANCED", "AGGRESSIVE", "BREAKOUT"]
PlanStatus = Literal["DRAFT", "BLOCKED", "WATCH_ONLY"]


class OrderPriceModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class OrderPriceInput(OrderPriceModel):
    stock_code: str
    stock_name: str
    industry: str | None = None
    side: Side = "BUY"
    committee_score: Decimal = Field(ge=0, le=100)
    recommendation: str
    risk_level: str
    confidence: Decimal = Field(ge=0, le=1)
    previous_close: Decimal
    latest_price: Decimal
    limit_up_price: Decimal
    limit_down_price: Decimal
    kline_bars: list[KlineBar]
    volume: int
    amount: Decimal
    turnover_rate: Decimal | None = None
    news_score: Decimal | None = None
    emotion_score: Decimal | None = None
    capital_score: Decimal | None = None


class PriceLevelCandidate(OrderPriceModel):
    price_type: PriceType
    price: Decimal
    score: Decimal = Field(ge=0, le=100)
    fill_probability: Decimal = Field(ge=0, le=1)
    expected_return: Decimal
    risk_reward: Decimal
    expected_profit_price: Decimal
    stop_loss_price: Decimal
    reason: str


class OrderPlanDraft(OrderPriceModel):
    stock_code: str
    stock_name: str
    plan_date: date
    plan_session: str
    side: Side
    strategy_type: str
    recommended_price: Decimal | None
    price_range_low: Decimal | None
    price_range_high: Decimal | None
    max_acceptable_price: Decimal | None
    stop_loss_price: Decimal | None
    take_profit_1_price: Decimal | None
    take_profit_2_price: Decimal | None
    suggested_position_percent: Decimal
    confidence: Decimal = Field(ge=0, le=1)
    reason: str
    valid_conditions: dict
    cancel_conditions: dict
    reprice_conditions: dict
    status: PlanStatus
    candidates: list[PriceLevelCandidate]


class OrderPriceRanking(OrderPriceModel):
    generated_at: datetime
    input_count: int
    returned_count: int
    plans: list[OrderPlanDraft]
