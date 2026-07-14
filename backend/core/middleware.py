from __future__ import annotations

import json
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from starlette.requests import Request


class TraceIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        trace_id = request.headers.get("X-Trace-Id") or str(uuid4())
        request.state.trace_id = trace_id

        response = await call_next(request)
        response.headers["X-Trace-Id"] = trace_id
        return response


class ApiContractEnvelopeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if not _uses_v03_api_contract(request.url.path):
            return response
        if not _is_json_response(response):
            return response

        body = b""
        async for chunk in response.body_iterator:
            body += chunk

        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _rebuild_response(response, body)

        converted = to_api_contract_payload(payload, getattr(request.state, "trace_id", None))
        if converted is payload:
            return _rebuild_response(response, body)

        content = json.dumps(converted, ensure_ascii=False).encode("utf-8")
        return _rebuild_response(response, content)


def to_api_contract_payload(payload, trace_id: str | None = None):
    if not isinstance(payload, dict):
        return payload
    if set(payload.keys()) == {"success", "data", "error", "trace_id"}:
        return payload
    if "success" not in payload or "data" not in payload:
        return payload

    response_trace_id = payload.get("trace_id") or trace_id
    if payload.get("success") is True:
        return {
            "success": True,
            "data": payload.get("data"),
            "error": None,
            "trace_id": response_trace_id,
        }

    details = payload.get("data")
    return {
        "success": False,
        "data": None,
        "error": {
            "code": payload.get("code") or "ERROR",
            "message": payload.get("message") or "Request failed",
            "details": details if isinstance(details, dict) else {},
        },
        "trace_id": response_trace_id,
    }


def _uses_v03_api_contract(path: str) -> bool:
    return path.startswith("/api/") and not path.startswith("/api/v1/")


def _is_json_response(response) -> bool:
    media_type = response.headers.get("content-type", "")
    return "application/json" in media_type.lower()


def _rebuild_response(response, content: bytes) -> Response:
    headers = dict(response.headers)
    headers.pop("content-length", None)
    return Response(
        content=content,
        status_code=response.status_code,
        headers=headers,
        media_type="application/json",
        background=response.background,
    )
