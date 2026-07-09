from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from backend.core.config_manager import ConfigManager, ConfigManagerError
from backend.core.exceptions import AppException
from backend.core.responses import success_response


router = APIRouter(prefix="/config", tags=["config"])


class ConfigValueBody(BaseModel):
    value: Any
    user: str = "local_admin"
    reason: str = "frontend update"


class ConfigBulkItem(BaseModel):
    config_key: str
    value: Any


class ConfigBulkBody(BaseModel):
    items: list[ConfigBulkItem] = Field(default_factory=list)
    user: str = "local_admin"
    reason: str = "bulk update from frontend"


class ConfigResetBody(BaseModel):
    user: str = "local_admin"
    reason: str = "frontend reset"


@router.get("/effective")
def get_effective_config(request: Request) -> dict:
    manager = ConfigManager()
    return success_response(
        data=manager.get_effective_config(),
        trace_id=request.state.trace_id,
    )


@router.get("/editable")
def get_editable_config(request: Request) -> dict:
    manager = ConfigManager()
    return success_response(
        data={"items": manager.list_editable_config()},
        trace_id=request.state.trace_id,
    )


@router.put("/values/{config_key}")
def update_config_value(config_key: str, body: ConfigValueBody, request: Request) -> dict:
    manager = ConfigManager()
    try:
        data = manager.set_config_value(
            key=config_key,
            value=body.value,
            user=body.user,
            reason=body.reason,
        )
    except ConfigManagerError as exc:
        raise _to_app_exception(exc) from exc
    return success_response(data=data, trace_id=request.state.trace_id)


@router.post("/bulk")
def update_config_bulk(body: ConfigBulkBody, request: Request) -> dict:
    manager = ConfigManager()
    try:
        data = manager.set_config_values_bulk(
            items=[item.model_dump() for item in body.items],
            user=body.user,
            reason=body.reason,
        )
    except ConfigManagerError as exc:
        raise _to_app_exception(exc) from exc
    return success_response(data=data, trace_id=request.state.trace_id)


@router.post("/values/{config_key}/reset")
def reset_config_value(
    config_key: str,
    request: Request,
    body: ConfigResetBody | None = None,
) -> dict:
    manager = ConfigManager()
    payload = body or ConfigResetBody()
    try:
        data = manager.reset_config_value(
            key=config_key,
            user=payload.user,
            reason=payload.reason,
        )
    except ConfigManagerError as exc:
        raise _to_app_exception(exc) from exc
    return success_response(data=data, trace_id=request.state.trace_id)


@router.get("/history")
def get_config_history(
    request: Request,
    config_key: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict:
    manager = ConfigManager()
    try:
        rows = manager.list_config_history(config_key=config_key, limit=limit)
    except ConfigManagerError as exc:
        raise _to_app_exception(exc) from exc
    return success_response(data={"items": rows}, trace_id=request.state.trace_id)


def _to_app_exception(exc: ConfigManagerError) -> AppException:
    return AppException(
        code=exc.code,
        message=exc.message,
        status_code=exc.status_code,
        data=exc.data,
    )
