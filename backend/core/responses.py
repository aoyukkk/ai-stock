from __future__ import annotations

from typing import Any
from uuid import uuid4


def success_response(
    data: Any | None = None,
    message: str = "ok",
    code: str = "OK",
    trace_id: str | None = None,
) -> dict[str, Any]:
    return {
        "success": True,
        "code": code,
        "message": message,
        "data": data,
        "trace_id": trace_id or str(uuid4()),
    }


def error_response(
    code: str,
    message: str,
    data: Any | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    return {
        "success": False,
        "code": code,
        "message": message,
        "data": data,
        "trace_id": trace_id or str(uuid4()),
    }
