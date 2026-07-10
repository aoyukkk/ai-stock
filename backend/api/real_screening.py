from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from backend.core.responses import error_response, success_response
from backend.core.security import sanitize_config
from screening.real_validation import RealLightScreeningValidationService
from database.models.quant_run import QuantRun
from database.models.temporal import RunDataManifestRecord
from database.session import get_session


router = APIRouter(prefix="/api/pools", tags=["manual-llm-screening"])


class RealLightScreeningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quant_run_id: str | None = Field(default=None, max_length=128)
    sample_mode: Literal["STRATIFIED", "TOP_N"] = "STRATIFIED"
    sample_size: int = Field(default=3, ge=1, le=5)
    dry_run: bool = True
    use_real_provider: bool = False
    run_data_manifest_id: str | None = Field(default=None, max_length=128)


@router.post("/run-llm-screening")
def run_llm_screening(body: RealLightScreeningRequest, request: Request) -> dict:
    try:
        if body.use_real_provider:
            session = get_session()
            try:
                quant_run = session.scalar(select(QuantRun).where(QuantRun.run_id == body.quant_run_id)) if body.quant_run_id else None
                manifest = session.scalar(select(RunDataManifestRecord).where(RunDataManifestRecord.manifest_id == body.run_data_manifest_id)) if body.run_data_manifest_id else None
                if quant_run is None or manifest is None or not quant_run.actionable or not manifest.actionable or quant_run.data_manifest_id != manifest.manifest_id:
                    return error_response(code="REAL_LLM_TEMPORAL_GATE_BLOCKED", message="Real light screening requires an actionable Quant Run and matching manifest.", trace_id=request.state.trace_id)
            finally:
                session.close()
        result = RealLightScreeningValidationService().run(
            quant_run_id=body.quant_run_id,
            sample_mode=body.sample_mode,
            sample_size=body.sample_size,
            dry_run=body.dry_run,
            use_real_provider=body.use_real_provider,
        )
    except ValueError as exc:
        return error_response(
            code="LLM_SCREENING_REQUEST_INVALID",
            message=str(exc),
            trace_id=request.state.trace_id,
        )
    except Exception:
        return error_response(
            code="LLM_SCREENING_FAILED",
            message="Light-screening validation failed safely.",
            trace_id=request.state.trace_id,
        )
    return success_response(
        data=sanitize_config(result.model_dump(mode="json")),
        trace_id=request.state.trace_id,
    )
