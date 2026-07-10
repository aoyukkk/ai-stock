from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IndustryChain(StrictModel):
    chain_name: str | None = None
    chain_position: Literal["UPSTREAM_RESOURCE", "UPSTREAM_MATERIAL", "MIDSTREAM_COMPONENT", "MIDSTREAM_EQUIPMENT", "DOWNSTREAM_PRODUCT", "DOWNSTREAM_APPLICATION", "SERVICE_PLATFORM", "MULTI_SEGMENT", "UNKNOWN"] = "UNKNOWN"
    direct_or_indirect: Literal["DIRECT", "INDIRECT", "UNKNOWN"] = "UNKNOWN"
    source_status: Literal["LLM_UNVERIFIED"] = "LLM_UNVERIFIED"
    confidence: float = Field(ge=0, le=0.40)
    reason: str
    evidence_fields: list[str] = Field(default_factory=list)


class IndustryPosition(StrictModel):
    level: Literal["MAJOR_PARTICIPANT", "SECOND_TIER", "NICHE_PLAYER", "UNCLEAR"] = "UNCLEAR"
    description: str
    source_status: Literal["LLM_UNVERIFIED"] = "LLM_UNVERIFIED"
    confidence: float = Field(ge=0, le=0.30)
    limitations: list[str] = Field(default_factory=list)


class MainTheme(StrictModel):
    theme: str | None = None
    strength: int = Field(ge=0, le=100)
    core_beneficiary: bool = False
    source_status: Literal["LLM_UNVERIFIED"] = "LLM_UNVERIFIED"
    confidence: float = Field(ge=0, le=0.35)
    valid_until: date | None = None


class DomesticSubstitution(StrictModel):
    level: Literal["NONE", "WEAK", "MODERATE", "INSUFFICIENT_DATA"] = "INSUFFICIENT_DATA"
    target_product: str | None = None
    commercialization_stage: str | None = None
    source_status: Literal["LLM_UNVERIFIED"] = "LLM_UNVERIFIED"
    confidence: float = Field(ge=0, le=0.25)


class GenericInference(StrictModel):
    summary: str
    source_status: Literal["LLM_UNVERIFIED"] = "LLM_UNVERIFIED"
    confidence: float = Field(ge=0, le=0.40)


class FundamentalInference(StrictModel):
    stock_code: str
    as_of_time: datetime
    research_mode: Literal["DEEPSEEK_UNVERIFIED"] = "DEEPSEEK_UNVERIFIED"
    industry_chain: IndustryChain
    level_one_sector_explanation: GenericInference
    main_business_summary: GenericInference
    industry_position: IndustryPosition
    concept_tags: list[dict] = Field(default_factory=list)
    main_theme: MainTheme
    competitive_advantage: GenericInference
    industry_trend: GenericInference
    investment_logic: GenericInference
    domestic_substitution: DomesticSubstitution
    observation_rating: Literal["CORE_TRACK", "KEY_WATCH", "NORMAL_WATCH", "LOW_PRIORITY", "AVOID", "INSUFFICIENT_DATA"]
    financial_status: dict
    data_conflict: bool = False
    missing_fields: list[str] = Field(default_factory=list)
    requires_manual_review: bool = True
    display_marker: Literal["*"] = "*"
    current_market_main_theme: Literal["UNKNOWN"] = "UNKNOWN"
    latest_industry_event: Literal["UNKNOWN"] = "UNKNOWN"
    latest_company_event: Literal["UNKNOWN"] = "UNKNOWN"
    current_policy_catalyst: Literal["UNKNOWN"] = "UNKNOWN"
    current_news_catalyst: Literal["UNKNOWN"] = "UNKNOWN"
    current_market_sentiment_from_news: Literal["UNKNOWN"] = "UNKNOWN"

    @model_validator(mode="after")
    def dynamic_fields_require_timestamped_evidence(self):
        self.current_market_main_theme = "UNKNOWN"
        self.latest_industry_event = "UNKNOWN"
        self.latest_company_event = "UNKNOWN"
        self.current_policy_catalyst = "UNKNOWN"
        self.current_news_catalyst = "UNKNOWN"
        self.current_market_sentiment_from_news = "UNKNOWN"
        self.main_theme.theme = None
        self.main_theme.strength = 0
        self.main_theme.core_beneficiary = False
        return self
