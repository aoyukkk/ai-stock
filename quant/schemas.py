from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from datasource.schemas import (
    CapitalFlowSnapshot,
    FinanceSnapshot,
    KlineBar,
    MarketEmotionSnapshot,
    RealtimeQuote,
)


class QuantModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class QuantFactorInput(QuantModel):
    stock_code: str
    stock_name: str
    industry: str | None = None
    realtime_quote: RealtimeQuote
    kline_bars: list[KlineBar]
    finance_snapshot: FinanceSnapshot | None = None
    capital_flow: CapitalFlowSnapshot | None = None
    market_emotion: MarketEmotionSnapshot | None = None
    raw_kline_bars: list[KlineBar] | None = None
    price_adjustment: dict[str, Any] = Field(default_factory=dict)
    price_limit_risk: dict[str, Any] = Field(default_factory=dict)


class QuantFactorScore(QuantModel):
    stock_code: str
    factor_group: str
    factor_name: str
    raw_value: Decimal | None = None
    normalized_value: Decimal | None = None
    score: Decimal = Field(ge=0, le=100)
    weight: Decimal | None = None
    explain_text: str


class QuantScoreResult(QuantModel):
    stock_code: str
    stock_name: str
    industry: str | None = None
    technical_score: Decimal = Field(ge=0, le=100)
    capital_score: Decimal = Field(ge=0, le=100)
    emotion_score: Decimal = Field(ge=0, le=100)
    momentum_score: Decimal = Field(ge=0, le=100)
    risk_score: Decimal = Field(ge=0, le=100)
    total_score: Decimal = Field(ge=0, le=100)
    rank: int | None = None
    factor_details: list[QuantFactorScore] = Field(default_factory=list)
    reason: str
    factor_version: str


class QuantRankingResult(QuantModel):
    generated_at: datetime
    universe_size: int
    requested_top_q: int
    returned_count: int
    factor_version: str
    results: list[QuantScoreResult]
