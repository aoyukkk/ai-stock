from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


Direction = Literal["BUY", "WATCH", "NEUTRAL", "AVOID"]


class ScreeningModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class LightScreeningInput(ScreeningModel):
    stock_code: str
    stock_name: str
    industry: str | None = None
    quant_rank: int
    quant_total_score: Decimal
    technical_score: Decimal
    capital_score: Decimal
    emotion_score: Decimal
    momentum_score: Decimal
    risk_score: Decimal
    quant_reason: str
    latest_news_summary: str | None = None
    overseas_summary: str | None = None
    liquidity_summary: str | None = None


class LightScreeningLLMOutput(ScreeningModel):
    stock_code: str
    opportunity_score: Decimal = Field(ge=0, le=100)
    event_catalyst_score: Decimal = Field(ge=0, le=100)
    sector_strength_score: Decimal = Field(ge=0, le=100)
    order_friendliness_score: Decimal = Field(ge=0, le=100)
    liquidity_score: Decimal = Field(ge=0, le=100)
    risk_penalty_score: Decimal = Field(ge=0, le=100)
    confidence: Decimal = Field(ge=0, le=1)
    direction: Direction
    reason: str
    risk_note: str
    should_keep: bool


class LightScreeningResult(ScreeningModel):
    stock_code: str
    stock_name: str
    industry: str | None = None
    quant_rank: int
    quant_total_score: Decimal
    llm_score: Decimal = Field(ge=0, le=100)
    final_light_score: Decimal = Field(ge=0, le=100)
    rank: int | None = None
    direction: Direction
    confidence: Decimal = Field(ge=0, le=1)
    should_keep: bool
    reason: str
    risk_note: str
    prompt_version: str | None = None
    model_name: str | None = None
    request_hash: str | None = None


class LightScreeningRanking(ScreeningModel):
    generated_at: datetime
    quant_universe_size: int
    requested_quant_top_q: int
    requested_top_n: int
    returned_count: int
    results: list[LightScreeningResult]
