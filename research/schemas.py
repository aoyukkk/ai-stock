from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ResearchStatus(StrEnum):
    VERIFIED = "VERIFIED"
    NO_VERIFIED_EVIDENCE = "NO_VERIFIED_EVIDENCE"
    WEB_SEARCH_UNVERIFIED = "WEB_SEARCH_UNVERIFIED"
    CAPABILITY_NOT_AVAILABLE = "CAPABILITY_NOT_AVAILABLE_FOR_APPLICATION_API"


class ResearchQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stock_code: str = Field(min_length=1, max_length=32)
    query: str = Field(min_length=1, max_length=500)
    template_id: str = Field(default="A", pattern="^[A-D]$")
    max_sources: int = Field(default=12, ge=1, le=12)


class ResearchEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stock_code: str
    query: str
    url: str
    canonical_url: str
    domain: str
    title: str = Field(max_length=500)
    snippet: str = Field(max_length=4000)
    published_at: datetime | None = None
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source_tier: str = Field(pattern="^tier_[1-4]$")
    source_type: str
    credibility_score: float = Field(ge=0, le=1)
    content_hash: str = Field(pattern="^[0-9a-f]{64}$")
    provider: str
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
    related_fields: list[str] = Field(default_factory=list)
    source_temporal_status: str = "PUBLISH_TIME_UNKNOWN"
    query_time: datetime | None = None

    @field_validator("url", "canonical_url")
    @classmethod
    def require_http_url(cls, value: str) -> str:
        if not value.startswith(("https://", "http://")):
            raise ValueError("verified evidence requires an HTTP(S) URL")
        return value


class ResearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ResearchStatus
    provider: str
    stock_code: str
    query: str
    evidence: list[ResearchEvidence] = Field(default_factory=list, max_length=12)
    summary: str | None = None
    warnings: list[str] = Field(default_factory=list)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("evidence")
    @classmethod
    def verified_status_needs_evidence(cls, value: list[ResearchEvidence]) -> list[ResearchEvidence]:
        return value
