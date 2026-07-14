from __future__ import annotations

import hmac
import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class DesktopLocalAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not _desktop_mode() or request.url.path == "/health":
            return await call_next(request)
        expected = os.getenv("AI_TRADER_LOCAL_API_TOKEN", "")
        supplied = request.headers.get("X-AI-Trader-Token", "")
        if not expected or not hmac.compare_digest(expected, supplied):
            return JSONResponse(
                status_code=401,
                content={
                    "success": False,
                    "data": None,
                    "error": {"code": "LOCAL_API_UNAUTHORIZED", "message": "Local API authentication failed.", "details": {}},
                    "trace_id": getattr(request.state, "trace_id", None),
                },
            )
        return await call_next(request)


def _desktop_mode() -> bool:
    return os.getenv("AI_TRADER_DESKTOP_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}

