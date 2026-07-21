from __future__ import annotations

import os
import asyncio
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.core.responses import error_response, success_response
from backend.version import APP_VERSION, SCHEMA_VERSION


router = APIRouter(prefix="/api/runtime", tags=["desktop-runtime"])
version_router = APIRouter(tags=["desktop-runtime"])
SECRET_ENV = {
    "tushare": "TUSHARE_TOKEN", "deepseek": "DEEPSEEK_API_KEY",
    "openai": "OPENAI_API_KEY", "tavily": "TAVILY_API_KEY",
    "ifind_username": "IFIND_USERNAME", "ifind_password": "IFIND_PASSWORD",
    "ifind_access": "IFIND_ACCESS_TOKEN", "ifind_refresh": "IFIND_REFRESH_TOKEN",
}
SecretProvider = Literal[
    "tushare", "deepseek", "openai", "tavily",
    "ifind_username", "ifind_password", "ifind_access", "ifind_refresh",
]


class RuntimeSecretRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: SecretProvider
    value: str = Field(min_length=1, max_length=512)


@router.get("/version")
@version_router.get("/api/version")
def version(request: Request) -> dict:
    return success_response(
        data={"app_version": APP_VERSION, "schema_version": SCHEMA_VERSION, "desktop_mode": _desktop_mode()},
        trace_id=request.state.trace_id,
    )


@router.post("/secrets")
def inject_secret(body: RuntimeSecretRequest, request: Request) -> dict:
    if not _desktop_mode():
        return error_response("DESKTOP_MODE_REQUIRED", "Runtime secret injection is desktop-only.", trace_id=request.state.trace_id)
    value = body.value.strip()
    minimum_length = 1 if body.provider == "ifind_username" else 8
    if len(value) < minimum_length:
        body.value = ""
        return error_response("SECRET_UPDATE_INVALID", "Secret value is too short.", trace_id=request.state.trace_id)
    env_name = SECRET_ENV[body.provider]
    os.environ[env_name] = value
    body.value = ""
    return success_response(
        data={"provider": body.provider, "configured": True, "updated_at": datetime.now(timezone.utc).isoformat()},
        trace_id=request.state.trace_id,
    )


@router.delete("/secrets/{provider}")
def clear_secret(provider: SecretProvider, request: Request) -> dict:
    os.environ.pop(SECRET_ENV[provider], None)
    return success_response(data={"provider": provider, "configured": False}, trace_id=request.state.trace_id)


@router.post("/shutdown")
async def shutdown(request: Request) -> dict:
    server = getattr(request.app.state, "uvicorn_server", None)
    if server is None:
        return error_response("SHUTDOWN_UNAVAILABLE", "Desktop server handle is unavailable.", trace_id=request.state.trace_id)
    asyncio.get_running_loop().call_later(0.1, setattr, server, "should_exit", True)
    return success_response(data={"status": "SHUTTING_DOWN"}, trace_id=request.state.trace_id)


def _desktop_mode() -> bool:
    return os.getenv("AI_TRADER_DESKTOP_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}
