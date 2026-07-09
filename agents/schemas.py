from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


Direction = Literal["BUY", "WATCH", "NEUTRAL", "AVOID"]
AgentAction = Literal["ALLOW", "WATCH_ONLY", "BLOCK", "NEED_RECHECK"]
Recommendation = Literal["STRONG_WATCH", "WATCH", "NEUTRAL", "AVOID", "BLOCKED"]
RiskLevel = Literal["LOW", "MEDIUM", "HIGH", "BLACK_SWAN"]


class AgentModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CommitteeInput(AgentModel):
    stock_code: str
    stock_name: str
    industry: str | None = None
    quant_rank: int
    quant_total_score: Decimal = Field(ge=0, le=100)
    light_rank: int
    final_light_score: Decimal = Field(ge=0, le=100)
    light_direction: Direction
    light_reason: str
    technical_summary: str
    capital_summary: str
    emotion_summary: str
    news_summary: str
    overseas_summary: str
    risk_summary: str


class AgentAnalysisOutput(AgentModel):
    agent_name: str
    stock_code: str
    score: Decimal = Field(ge=0, le=100)
    direction: Direction
    confidence: Decimal = Field(ge=0, le=1)
    reason: str
    risk_note: str
    action: AgentAction
    raw_output: dict | None = None
    prompt_version: str | None = None
    model_version: str | None = None
    request_hash: str | None = None


class CommitteeStockResult(AgentModel):
    stock_code: str
    stock_name: str
    industry: str | None = None
    quant_score: Decimal = Field(ge=0, le=100)
    light_score: Decimal = Field(ge=0, le=100)
    technical_score: Decimal = Field(ge=0, le=100)
    news_score: Decimal = Field(ge=0, le=100)
    capital_score: Decimal = Field(ge=0, le=100)
    emotion_score: Decimal = Field(ge=0, le=100)
    overseas_score: Decimal = Field(ge=0, le=100)
    risk_score: Decimal = Field(ge=0, le=100)
    final_score: Decimal = Field(ge=0, le=100)
    recommendation: Recommendation
    risk_level: RiskLevel
    confidence: Decimal = Field(ge=0, le=1)
    controller_reason: str
    risk_note: str
    agent_outputs: list[AgentAnalysisOutput]
    rank: int | None = None
    prompt_version: str | None = None
    model_version: str | None = None


class CommitteeRanking(AgentModel):
    generated_at: datetime
    input_count: int
    requested_top_n: int
    returned_count: int
    results: list[CommitteeStockResult]
