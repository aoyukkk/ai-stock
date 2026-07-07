from typing import Any

from pydantic import BaseModel, Field


class APIResponse(BaseModel):
    success: bool
    data: Any = Field(default_factory=dict)
    message: str = ""


def success_response(data: Any | None = None, message: str = "") -> dict[str, Any]:
    return APIResponse(
        success=True,
        data={} if data is None else data,
        message=message,
    ).model_dump()


def error_response(message: str, data: Any | None = None) -> dict[str, Any]:
    return APIResponse(
        success=False,
        data={} if data is None else data,
        message=message,
    ).model_dump()
