from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from database.models.research import PendingVerificationTask, ResearchEvidenceRecord
from database.models.validation import (
    ModelValidationAllocation,
    ModelValidationFailureAudit,
    ModelValidationLLMAudit,
    ModelValidationOrderPlan,
    ModelValidationSample,
)
from research.evidence import canonicalize_url, evidence_hash
from research.repository import FundamentalRepository
from research.schemas import ResearchEvidence
from stock_codes import normalize_ts_code


REQUIRED_EVIDENCE_FIELDS = {
    "main_business_summary",
    "industry_chain",
    "competitive_advantage",
    "investment_logic",
    "financial_status_explanation",
}


class VerifiedEvidenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=3, max_length=500)
    url: str
    published_at: datetime | None = None
    snippet: str = Field(min_length=10, max_length=4000)
    source_tier: Literal["tier_1", "tier_2", "tier_3", "tier_4"] = "tier_1"
    source_type: str = Field(min_length=3, max_length=64)
    credibility_score: float = Field(ge=0, le=1)
    related_fields: list[str] = Field(min_length=1, max_length=20)

    @field_validator("url")
    @classmethod
    def require_https(cls, value: str) -> str:
        canonical = canonicalize_url(value)
        if not canonical.startswith("https://"):
            raise ValueError("verified external evidence requires HTTPS")
        return value


class VerifiedFundamentalProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    industry_chain_name: str = Field(min_length=4, max_length=120)
    chain_position: Literal["UPSTREAM", "MIDSTREAM", "DOWNSTREAM", "SERVICE_PLATFORM", "MULTI_SEGMENT"]
    main_business_summary: str = Field(min_length=20, max_length=1000)
    core_products: list[str] = Field(min_length=1, max_length=20)
    concept_tags: list[str] = Field(default_factory=list, max_length=20)
    industry_position_level: Literal["MAJOR_PARTICIPANT", "SECOND_TIER", "NICHE_PLAYER", "UNCLEAR"]
    industry_position_description: str = Field(min_length=10, max_length=500)
    structural_theme_fit: str = Field(min_length=10, max_length=500)
    competitive_advantage: str = Field(min_length=20, max_length=800)
    industry_trend: str = Field(min_length=20, max_length=800)
    investment_logic: str = Field(min_length=20, max_length=1200)
    invalidation_conditions: list[str] = Field(min_length=1, max_length=10)
    domestic_substitution: Literal["NONE", "WEAK", "MODERATE", "INSUFFICIENT_DATA"]
    observation_rating: Literal["CORE_TRACK", "KEY_WATCH", "NORMAL_WATCH", "LOW_PRIORITY", "AVOID"]
    financial_status_explanation: str = Field(min_length=20, max_length=1000)
    key_risks: list[str] = Field(min_length=1, max_length=12)
    financial_snapshot: dict[str, Any] = Field(default_factory=dict)
    verified_customers: list[str] = Field(default_factory=list, max_length=20)


class VerifiedFundamentalCompletionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    validation_run_id: str = Field(min_length=3, max_length=128)
    stock_code: str = Field(min_length=6, max_length=32)
    as_of_time: datetime
    query: str = Field(min_length=3, max_length=500)
    profile: VerifiedFundamentalProfileInput
    evidence: list[VerifiedEvidenceInput] = Field(min_length=2, max_length=12)

    @model_validator(mode="after")
    def require_primary_source_coverage(self):
        if not any(item.source_tier == "tier_1" for item in self.evidence):
            raise ValueError("at least one tier_1 source is required")
        covered = {field for item in self.evidence for field in item.related_fields}
        missing = REQUIRED_EVIDENCE_FIELDS - covered
        if missing:
            raise ValueError("evidence coverage missing: " + ",".join(sorted(missing)))
        return self


class VerifiedFundamentalCompletionService:
    """Apply a source-backed fundamental correction without re-running Flash."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = FundamentalRepository(session)

    def apply(self, request: VerifiedFundamentalCompletionInput) -> dict[str, Any]:
        code = normalize_ts_code(request.stock_code)
        sample = self.session.scalar(
            select(ModelValidationSample).where(
                ModelValidationSample.validation_run_id == request.validation_run_id,
                ModelValidationSample.stock_code.in_([code, code.split(".", 1)[0]]),
            )
        )
        if sample is None:
            raise ValueError("VALIDATION_SAMPLE_NOT_FOUND")

        payload_hash = _payload_hash(request)
        research_run_id = f"external-research-{payload_hash[:20]}"
        evidence = self._evidence(request, code)
        field_evidence = _field_evidence(evidence)
        profile_version = f"external-verified-v1-{payload_hash[:12]}"

        self.repository.save_run({
            "run_id": research_run_id,
            "provider": "manual_web_research",
            "mode": "EXTERNAL_VERIFIED",
            "status": "COMPLETE",
            "dry_run": False,
            "stock_codes": [code],
            "query_count": 1,
            "source_count": len(evidence),
            "capability_metadata": {
                "audit_mode": "URL_AND_CLAIM_HASH",
                "llm_calls": 0,
                "flash_rerun": False,
            },
            "request_hash": payload_hash,
            "config_snapshot": {"schema_version": "external_verified_fundamental_v1"},
        })
        inserted = self.repository.save_evidence(research_run_id, evidence)

        fundamental = self._domain_result(
            request, sample, code, research_run_id, profile_version, field_evidence
        )
        self.repository.save_profile({
            "stock_code": code,
            "version": profile_version,
            "research_run_id": research_run_id,
            "profile": fundamental,
            "field_evidence": field_evidence,
            "missing_fields": [],
            "conflicts": {},
            "verified_evidence_count": len(evidence),
            "suitable_for_score_boost": False,
            "field_provenance_map": {
                field: {
                    "source_status": "WEB_VERIFIED",
                    "verified": True,
                    "evidence_hashes": hashes,
                }
                for field, hashes in field_evidence.items()
            },
            "available_at": request.as_of_time,
            "request_hash": payload_hash,
        })

        sample.fundamental_result = fundamental
        sample.missing_fields = [
            field for field in (sample.missing_fields or [])
            if field not in field_evidence and field != "concept_tags"
        ]
        screening = json.loads(json.dumps(sample.screening_result or {}, default=str))
        metadata = screening.setdefault("_trader_demo", {})
        metadata["errors"] = [
            item for item in metadata.get("errors") or []
            if item.get("task") != "fundamental_structured_inference"
        ]
        if not metadata["errors"]:
            metadata["execution_status"] = "SUCCESS"
        metadata.update({
            "fundamental_completion_status": "EXTERNAL_VERIFIED",
            "fundamental_research_run_id": research_run_id,
            "fundamental_profile_version": profile_version,
            "flash_rerun": False,
        })
        screening["verified_fundamental_risk_note"] = "；".join(request.profile.key_risks)
        sample.screening_result = screening
        flag_modified(sample, "fundamental_result")
        flag_modified(sample, "screening_result")
        flag_modified(sample, "missing_fields")
        resolved_audits = self._resolve_failures(request.validation_run_id, code, research_run_id)
        self._resolve_pending_tasks(code, field_evidence)
        self._mark_downstream_review_pending(request.validation_run_id, code)
        self.session.commit()

        return {
            "validation_run_id": request.validation_run_id,
            "stock_code": code,
            "research_run_id": research_run_id,
            "profile_version": profile_version,
            "analysis_status": "SUCCESS",
            "evidence_count": len(evidence),
            "evidence_inserted": inserted,
            "resolved_audit_count": resolved_audits,
            "downstream_status": "REVIEW_PENDING",
            "flash_rerun": False,
            "llm_calls": 0,
            "payload_hash": payload_hash,
        }

    @staticmethod
    def _evidence(
        request: VerifiedFundamentalCompletionInput, code: str
    ) -> list[ResearchEvidence]:
        result = []
        now = datetime.now(timezone.utc)
        for item in request.evidence:
            canonical = canonicalize_url(item.url)
            result.append(ResearchEvidence(
                stock_code=code,
                query=request.query,
                url=item.url,
                canonical_url=canonical,
                domain=urlsplit(canonical).hostname or "unknown",
                title=item.title,
                snippet=item.snippet,
                published_at=item.published_at,
                retrieved_at=now,
                source_tier=item.source_tier,
                source_type=item.source_type,
                credibility_score=item.credibility_score,
                content_hash=evidence_hash(canonical, item.title, item.snippet),
                provider="manual_web_research",
                provider_metadata={"capture_mode": "human_verified_web_source"},
                related_fields=item.related_fields,
                source_temporal_status="PUBLISHED_BEFORE_AS_OF_TIME",
                query_time=now,
            ))
        return result

    @staticmethod
    def _domain_result(
        request: VerifiedFundamentalCompletionInput,
        sample: ModelValidationSample,
        code: str,
        research_run_id: str,
        profile_version: str,
        field_evidence: dict[str, list[str]],
    ) -> dict[str, Any]:
        profile = request.profile
        prior = sample.fundamental_result or {}

        def sourced(field: str, key: str, value: Any) -> dict[str, Any]:
            return {
                key: value,
                "source_status": "WEB_VERIFIED",
                "verified": True,
                "confidence": 0.95,
                "evidence_fields": field_evidence.get(field, []),
                "display_marker": "",
            }

        return {
            "stock_code": code,
            "as_of_time": request.as_of_time.isoformat(),
            "research_mode": "EXTERNAL_VERIFIED",
            "analysis_status": "SUCCESS",
            "wire_schema_version": "external_verified_fundamental_v1",
            "profile_version": profile_version,
            "industry_chain": {
                "chain_name": profile.industry_chain_name,
                "chain_position": profile.chain_position,
                "source_status": "WEB_VERIFIED",
                "verified": True,
                "confidence": 0.95,
                "evidence_fields": field_evidence.get("industry_chain", []),
                "display_marker": "",
            },
            "level_one_sector_explanation": prior.get("level_one_sector_explanation") or {},
            "main_business_summary": sourced(
                "main_business_summary", "summary", profile.main_business_summary
            ),
            "core_products": profile.core_products,
            "concept_tags": [
                {
                    "name": name,
                    "source_status": "WEB_VERIFIED",
                    "verified": True,
                    "evidence_fields": field_evidence.get("concept_tags", []),
                    "display_marker": "",
                }
                for name in profile.concept_tags
            ],
            "industry_position": sourced(
                "industry_position", "description", profile.industry_position_description
            ) | {"level": profile.industry_position_level},
            "structural_theme_fit": sourced(
                "structural_theme_fit", "value", profile.structural_theme_fit
            ),
            "competitive_advantage": sourced(
                "competitive_advantage", "summary", profile.competitive_advantage
            ),
            "industry_trend": sourced("industry_trend", "summary", profile.industry_trend),
            "investment_logic": sourced(
                "investment_logic", "summary", profile.investment_logic
            ),
            "invalidation_conditions": profile.invalidation_conditions,
            "domestic_substitution": sourced(
                "domestic_substitution", "level", profile.domestic_substitution
            ),
            "observation_rating": profile.observation_rating,
            "financial_status": prior.get("financial_status") or {},
            "financial_status_explanation": sourced(
                "financial_status_explanation", "summary", profile.financial_status_explanation
            ),
            "financial_snapshot": profile.financial_snapshot,
            "key_risks": profile.key_risks,
            "data_conflict": False,
            "missing_fields": [],
            "requires_manual_review": True,
            "display_marker": "",
            "external_research_audit": {
                "research_run_id": research_run_id,
                "profile_version": profile_version,
                "source_count": len({
                    item for values in field_evidence.values() for item in values
                }),
                "field_evidence": field_evidence,
                "verified_customers": profile.verified_customers,
                "customer_claim_usage": "AUDIT_ONLY_NOT_PASSED_TO_PRO_SCORING",
                "llm_calls": 0,
                "flash_rerun": False,
            },
        }

    def _resolve_failures(self, validation_run_id: str, code: str, research_run_id: str) -> int:
        resolved = 0
        llm_audits = self.session.scalars(select(ModelValidationLLMAudit).where(
            ModelValidationLLMAudit.validation_run_id == validation_run_id,
            ModelValidationLLMAudit.stock_code.in_([code, code.split(".", 1)[0]]),
            ModelValidationLLMAudit.task == "fundamental_structured_inference",
            ModelValidationLLMAudit.schema_status.notin_(["PASS", "RESOLVED_HISTORY"]),
        )).all()
        for row in llm_audits:
            row.status = "RESOLVED"
            row.schema_status = "RESOLVED_HISTORY"
            row.diagnostics = {
                **dict(row.diagnostics or {}),
                "resolution": "EXTERNAL_VERIFIED_EVIDENCE",
                "research_run_id": research_run_id,
            }
            flag_modified(row, "diagnostics")
            resolved += 1
        failures = self.session.scalars(select(ModelValidationFailureAudit).where(
            ModelValidationFailureAudit.validation_run_id == validation_run_id,
            ModelValidationFailureAudit.stock_code.in_([code, code.split(".", 1)[0]]),
            ModelValidationFailureAudit.task == "fundamental_structured_inference",
            ModelValidationFailureAudit.status != "RESOLVED",
        )).all()
        for row in failures:
            row.status = "RESOLVED"
            row.diagnostics = {
                **dict(row.diagnostics or {}),
                "resolution": "EXTERNAL_VERIFIED_EVIDENCE",
                "research_run_id": research_run_id,
            }
            flag_modified(row, "diagnostics")
            resolved += 1
        return resolved

    def _resolve_pending_tasks(
        self, code: str, field_evidence: dict[str, list[str]]
    ) -> None:
        rows = self.session.scalars(select(PendingVerificationTask).where(
            PendingVerificationTask.stock_code.in_([code, code.split(".", 1)[0]]),
            PendingVerificationTask.status == "PENDING",
            PendingVerificationTask.field_name.in_(list(field_evidence)),
        )).all()
        first_evidence = self.session.scalar(select(ResearchEvidenceRecord.id).where(
            ResearchEvidenceRecord.content_hash.in_([
                item for values in field_evidence.values() for item in values
            ])
        ).order_by(ResearchEvidenceRecord.id))
        for row in rows:
            row.status = "RESOLVED"
            row.source_status = "WEB_VERIFIED"
            row.verified_evidence_id = first_evidence

    def _mark_downstream_review_pending(self, validation_run_id: str, code: str) -> None:
        marker = "EXTERNAL_FUNDAMENTAL_COMPLETED_DOWNSTREAM_REVIEW_PENDING"
        plan = self.session.scalar(select(ModelValidationOrderPlan).where(
            ModelValidationOrderPlan.validation_run_id == validation_run_id,
            ModelValidationOrderPlan.stock_code.in_([code, code.split(".", 1)[0]]),
        ))
        if plan is not None:
            warnings = [
                item for item in (plan.warnings or [])
                if item != "人工选择，但LLM分析失败"
            ]
            plan.warnings = sorted(set([*warnings, marker]))
            flag_modified(plan, "warnings")
        allocation = self.session.scalar(select(ModelValidationAllocation).where(
            ModelValidationAllocation.validation_run_id == validation_run_id,
            ModelValidationAllocation.stock_code.in_([code, code.split(".", 1)[0]]),
        ))
        if allocation is not None:
            allocation.warnings = sorted(set([*(allocation.warnings or []), marker]))
            flag_modified(allocation, "warnings")


def _field_evidence(evidence: list[ResearchEvidence]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for item in evidence:
        for field in item.related_fields:
            result.setdefault(field, []).append(item.content_hash)
    return {key: sorted(set(value)) for key, value in sorted(result.items())}


def _payload_hash(request: VerifiedFundamentalCompletionInput) -> str:
    material = request.model_dump(mode="json")
    return hashlib.sha256(
        json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
