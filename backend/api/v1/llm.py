from __future__ import annotations

from pydantic import BaseModel
from fastapi import APIRouter, Request

from backend.core.config import get_app_config
from backend.core.responses import success_response
from backend.core.security import sanitize_config
from llm_gateway.service import get_llm_gateway_service


router = APIRouter(prefix="/llm", tags=["llm"])


class MockChatBody(BaseModel):
    agent_name: str
    task: str
    message: str


@router.get("/status")
def llm_status(request: Request) -> dict:
    data = get_llm_gateway_service().status_summary()
    data["real_trading_enabled"] = get_app_config().real_trading_enabled
    return success_response(
        data=sanitize_config(data),
        trace_id=request.state.trace_id,
    )


@router.post("/mock-chat")
def mock_chat(body: MockChatBody, request: Request) -> dict:
    response = get_llm_gateway_service().chat_simple(
        agent_name=body.agent_name,
        task=body.task,
        user_content=body.message,
        metadata={"api": "mock-chat"},
    )
    data = response.model_dump(mode="json")
    return success_response(
        data=sanitize_config(data),
        trace_id=request.state.trace_id,
    )


@router.get("/usage-summary")
def usage_summary(request: Request) -> dict:
    return success_response(
        data=sanitize_config(get_llm_gateway_service().get_usage_summary()),
        trace_id=request.state.trace_id,
    )
