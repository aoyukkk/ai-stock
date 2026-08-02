from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from backend.core.responses import error_response, success_response
from backend.core.security import sanitize_config
from database.models.research import FundamentalResearchRun, ResearchEvidenceRecord
from database.models.temporal import RunDataManifestRecord
from database.models.quant_run import QuantRun
from database.session import get_session, init_db
from fundamentals.pipeline import CachedTushareProfileService
from research.deepseek_unverified import DeepSeekUnverifiedResearchProvider
from research.repository import FundamentalRepository
from research.schemas import ResearchQuery
from research.search_chain import SearchProviderChain, default_search_providers
from quant.run_repository import QuantRunRepository


router = APIRouter(prefix="/fundamental-research", tags=["fundamental-research"])


class FundamentalResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quant_run_id: str | None = Field(default=None, max_length=128)
    stock_codes: list[str] = Field(default_factory=list, max_length=5)
    sample_mode: Literal["STRATIFIED", "TOP_N"] = "STRATIFIED"
    sample_size: int = Field(default=3, ge=1, le=5)
    research_mode: Literal["DEEPSEEK_UNVERIFIED"] = "DEEPSEEK_UNVERIFIED"
    dry_run: bool = True
    use_real_llm: bool = False
    use_real_provider: bool = False
    run_data_manifest_id: str | None = None


@router.post("/run")
def run_fundamental_research(body: FundamentalResearchRequest, request: Request) -> dict:
    if body.use_real_provider:
        return success_response(
            data={
                "status": "CAPABILITY_NOT_AVAILABLE_FOR_APPLICATION_API",
                "real_execution_allowed": False,
                "query_count": 0,
                "source_count": 0,
                "message": "Auditable DeepSeek application-API web search remains unavailable.",
            },
            trace_id=request.state.trace_id,
        )
    if body.use_real_llm and body.dry_run:
        return error_response("FUNDAMENTAL_REQUEST_INVALID", "dry_run cannot use a real LLM", trace_id=request.state.trace_id)
    selected = body.stock_codes[: body.sample_size]
    run_id = f"fundamental-{uuid4().hex}"
    request_hash = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
    init_db()
    session = get_session()
    try:
        repository = FundamentalRepository(session)
        temporal_manifest = None
        if body.use_real_llm:
            if not body.quant_run_id or not body.run_data_manifest_id:
                return error_response("REAL_LLM_QUANT_CONTEXT_REQUIRED", "Real LLM research requires a persisted Quant Run and data manifest.", trace_id=request.state.trace_id)
            quant_run = session.scalar(select(QuantRun).where(QuantRun.run_id == body.quant_run_id))
            temporal_manifest = session.scalar(select(RunDataManifestRecord).where(RunDataManifestRecord.manifest_id == body.run_data_manifest_id))
            if quant_run is None or temporal_manifest is None or not quant_run.actionable or not temporal_manifest.actionable or quant_run.data_manifest_id != temporal_manifest.manifest_id:
                return error_response("REAL_LLM_TEMPORAL_GATE_BLOCKED", "Quant Run or data manifest is not actionable.", trace_id=request.state.trace_id)
            selected = [row.stock_code for row in QuantRunRepository(session).samples(quant_run.run_id, body.sample_size)]
        existing = session.scalar(select(FundamentalResearchRun).where(FundamentalResearchRun.request_hash == request_hash))
        if existing:
            return success_response(data=_run_payload(existing), trace_id=request.state.trace_id)
        fallback = DeepSeekUnverifiedResearchProvider()
        chain = SearchProviderChain(default_search_providers(), fallback)
        profiles = []
        for stock_code in selected:
            profile = CachedTushareProfileService().build(
                stock_code,
                decision_time=(
                    temporal_manifest.decision_time
                    if temporal_manifest is not None
                    else datetime.now().astimezone()
                ),
            )
            chain_result = chain.run(
                ResearchQuery(stock_code=stock_code, query="fundamental profile missing fields"),
                profile.model_dump(mode="json"),
                use_real_llm=body.use_real_llm,
                temporal_manifest=temporal_manifest,
            )
            inference = chain_result.unverified_payload or {}
            repository.save_profile({
                "stock_code": stock_code,
                "version": profile.profile_version,
                "research_run_id": run_id,
                "profile": {
                    **profile.model_dump(mode="json"),
                    "unverified_inference": inference,
                    "inference_metadata": {
                        **fallback.last_usage,
                        "source_status": "LLM_UNVERIFIED",
                        "fallback_reason": "SEARCH_PROVIDERS_NOT_CONFIGURED",
                    },
                },
                "field_evidence": {},
                "missing_fields": profile.missing_fields,
                "conflicts": {},
                "verified_evidence_count": 0,
                "suitable_for_score_boost": False,
                "field_provenance_map": {
                    **{key: value.model_dump(mode="json") for key, value in profile.field_provenance_map.items()},
                    **{key: {"source_status": "LLM_UNVERIFIED", "display_marker": "*", "verified": False}
                       for key in inference if key not in {"stock_code", "as_of_time", "research_mode", "financial_status", "missing_fields", "data_conflict", "requires_manual_review", "display_marker"}},
                },
                "available_at": profile.available_at,
                "request_hash": hashlib.sha256(f"{request_hash}:{stock_code}".encode()).hexdigest(),
            })
            task_fields = {key: value for key, value in inference.items() if isinstance(value, (dict, list)) and key not in {"financial_status"}}
            repository.upsert_verification_tasks(stock_code, task_fields)
            profiles.append({
                "stock_code": stock_code,
                "profile_version": profile.profile_version,
                "degradation_chain": [item.model_dump(mode="json") for item in chain_result.attempts],
                "research_degradation_level": chain_result.degradation_level,
                "requires_manual_review": True,
            })
        row = repository.save_run({
            "run_id": run_id,
            "provider": "search_provider_chain",
            "mode": body.research_mode,
            "status": "DRY_RUN_COMPLETE" if body.dry_run else "COMPLETE",
            "dry_run": body.dry_run,
            "stock_codes": selected,
            "query_count": 0,
            "source_count": 0,
            "capability_metadata": {"degradation_chain": ["CACHE", "TAVILY", "BRAVE", "SEARXNG", "DEEPSEEK_UNVERIFIED", "UNKNOWN"]},
            "request_hash": request_hash,
            "config_snapshot": {"sample_mode": body.sample_mode, "sample_size": body.sample_size, "research_mode": body.research_mode},
        })
        return success_response(data={**_run_payload(row), "profiles": profiles}, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/profiles/{stock_code}")
def get_fundamental_profile(stock_code: str, request: Request) -> dict:
    init_db()
    session = get_session()
    try:
        row = FundamentalRepository(session).latest_profile(stock_code)
        if row is None:
            return error_response("FUNDAMENTAL_PROFILE_NOT_FOUND", "No fundamental profile exists.", trace_id=request.state.trace_id)
        return success_response(data=sanitize_config(row.profile), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/runs/{run_id}")
def get_fundamental_run(run_id: str, request: Request) -> dict:
    session = get_session()
    try:
        row = session.scalar(select(FundamentalResearchRun).where(FundamentalResearchRun.run_id == run_id))
        if row is None:
            return error_response("FUNDAMENTAL_RUN_NOT_FOUND", "Fundamental research run was not found.", trace_id=request.state.trace_id)
        return success_response(data=_run_payload(row), trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/runs/{run_id}/evidence")
def get_fundamental_evidence(run_id: str, request: Request) -> dict:
    session = get_session()
    try:
        rows = session.scalars(select(ResearchEvidenceRecord).where(ResearchEvidenceRecord.run_id == run_id)).all()
        evidence = [{"stock_code": row.stock_code, "url": row.url, "title": row.title, "source_tier": row.source_tier, "content_hash": row.content_hash} for row in rows]
        return success_response(data={"run_id": run_id, "evidence": evidence}, trace_id=request.state.trace_id)
    finally:
        session.close()


def _run_payload(row: FundamentalResearchRun) -> dict:
    return {"run_id": row.run_id, "status": row.status, "mode": row.mode, "provider": row.provider, "dry_run": row.dry_run, "stock_codes": row.stock_codes, "query_count": row.query_count, "source_count": row.source_count, "capability_metadata": row.capability_metadata}
