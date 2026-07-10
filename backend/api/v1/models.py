from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from backend.core.responses import success_response
from backend.core.security import sanitize_config
from llm_gateway.connectivity import run_connectivity_test
from llm_gateway.service import get_llm_gateway_service


router = APIRouter(prefix="/models", tags=["models"])


class ModelTestBody(BaseModel):
    model_alias: str = Field(default="light-screening-default", min_length=1, max_length=128)


@router.post("/test")
def test_model(body: ModelTestBody, request: Request) -> dict:
    data = run_connectivity_test(get_llm_gateway_service(), body.model_alias)
    return success_response(data=sanitize_config(data), trace_id=request.state.trace_id)
