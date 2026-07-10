from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from research.schemas import ResearchEvidence


PROFILE_FIELDS = (
    "industry_chain",
    "level_one_sector",
    "main_business",
    "industry_position",
    "concept_tags",
    "main_theme",
    "competitive_advantage",
    "industry_trend",
    "investment_logic",
    "domestic_substitution",
    "observation_rating",
    "financial_status",
)


class FundamentalProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stock_code: str
    version: str = "fundamental-profile-v1"
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    fields: dict[str, Any]
    field_evidence: dict[str, list[str]]
    missing_fields: list[str]
    conflicts: dict[str, list[Any]]
    verified_evidence_count: int
    suitable_for_score_boost: bool


class FundamentalProfileService:
    """Merges trusted structured data first, then URL-backed evidence-derived values."""

    def build(
        self,
        stock_code: str,
        structured: dict[str, Any] | None = None,
        evidence_values: dict[str, list[tuple[Any, ResearchEvidence]]] | None = None,
    ) -> FundamentalProfile:
        structured = structured or {}
        evidence_values = evidence_values or {}
        fields: dict[str, Any] = {}
        links: dict[str, list[str]] = {}
        conflicts: dict[str, list[Any]] = {}
        evidence_ids: set[str] = set()

        for field in PROFILE_FIELDS:
            trusted = structured.get(field)
            candidates = evidence_values.get(field, [])
            if trusted not in (None, "", []):
                fields[field] = trusted
                links[field] = []
                differing = [value for value, _ in candidates if value != trusted]
                if differing:
                    conflicts[field] = [trusted, *differing]
            elif candidates:
                value, first_evidence = candidates[0]
                fields[field] = value
                links[field] = [item.content_hash for _, item in candidates]
                evidence_ids.update(links[field])
                differing = [candidate for candidate, _ in candidates[1:] if candidate != value]
                if differing:
                    conflicts[field] = [value, *differing]

        missing = [field for field in PROFILE_FIELDS if field not in fields]
        verified_count = len(evidence_ids)
        return FundamentalProfile(
            stock_code=stock_code,
            fields=fields,
            field_evidence=links,
            missing_fields=missing,
            conflicts=conflicts,
            verified_evidence_count=verified_count,
            suitable_for_score_boost=verified_count > 0 and not conflicts,
        )
