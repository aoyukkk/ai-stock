from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import httpx

from datasource.ifind.http.endpoints import endpoint
from datasource.ifind.http.errors import IFindHttpError, IFindHttpErrorCategory, classify_http_error
from datasource.ifind.http.schemas import IFindHttpAuthResult


class IFindHttpAuthManager:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float = 30,
        refresh_token: str | None = None,
        access_token: str | None = None,
        client_factory: Callable[..., Any] = httpx.Client,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._refresh_token = (refresh_token if refresh_token is not None else os.getenv("IFIND_REFRESH_TOKEN", "")).strip()
        self._access_token = (access_token if access_token is not None else os.getenv("IFIND_ACCESS_TOKEN", "")).strip()
        self._client_factory = client_factory
        self.auth_calls = 0
        self.last_result = IFindHttpAuthResult(
            status="CONFIGURED" if self._access_token or self._refresh_token else "NOT_CONFIGURED",
            source="ENVIRONMENT",
            expires_at=None,
            latency_ms=None,
            error_category=None,
            response_schema_hash=None,
        )

    @property
    def has_refresh_token(self) -> bool:
        return bool(self._refresh_token)

    @property
    def has_access_token(self) -> bool:
        return bool(self._access_token)

    def access_token(self) -> str:
        if not self._access_token:
            raise IFindHttpError(IFindHttpErrorCategory.ACCESS_TOKEN_INVALID, "Access token is not configured")
        return self._access_token

    def refresh_access_token(self) -> IFindHttpAuthResult:
        if not self._refresh_token:
            raise IFindHttpError(IFindHttpErrorCategory.REFRESH_TOKEN_NOT_CONFIGURED, "Refresh token is not configured")
        auth_endpoint = endpoint("get_access_token")
        started = time.perf_counter()
        self.auth_calls += 1
        try:
            with self._client_factory(timeout=self.timeout_seconds) as client:
                response = client.post(
                    f"{self.base_url}{auth_endpoint.path}",
                    headers={"Content-Type": "application/json", "refresh_token": self._refresh_token},
                    json={},
                )
        except httpx.TimeoutException as exc:
            self._fail(IFindHttpErrorCategory.TIMEOUT)
            raise IFindHttpError(IFindHttpErrorCategory.TIMEOUT, "iFinD authentication timed out") from exc
        except httpx.HTTPError as exc:
            self._fail(IFindHttpErrorCategory.NETWORK_ERROR)
            raise IFindHttpError(IFindHttpErrorCategory.NETWORK_ERROR, "iFinD authentication network error") from exc

        latency_ms = round((time.perf_counter() - started) * 1000)
        payload = _json_mapping(response)
        schema_hash = response_schema_hash(payload)
        provider_code = _provider_code(payload)
        provider_message = _provider_message(payload)
        token = _find_value(payload, "access_token")
        if response.status_code >= 400 or not isinstance(token, str) or not token.strip() or not _provider_success(provider_code):
            category = classify_http_error(response.status_code, provider_code, provider_message, authentication=True)
            self.last_result = IFindHttpAuthResult(
                status="INVALID", source="REFRESH_TOKEN", expires_at=None, latency_ms=latency_ms,
                error_category=category.value, response_schema_hash=schema_hash,
            )
            raise IFindHttpError(category, f"iFinD authentication failed ({category.value})", status_code=response.status_code)

        self._access_token = token.strip()
        expires_at = _expires_at(payload)
        self.last_result = IFindHttpAuthResult(
            status="CONFIGURED", source="REFRESH_TOKEN", expires_at=expires_at,
            latency_ms=latency_ms, error_category=None, response_schema_hash=schema_hash,
        )
        return self.last_result

    def _fail(self, category: IFindHttpErrorCategory) -> None:
        self.last_result = IFindHttpAuthResult(
            status="UNKNOWN", source="REFRESH_TOKEN", expires_at=None, latency_ms=None,
            error_category=category.value, response_schema_hash=None,
        )


def _json_mapping(response: Any) -> dict[str, Any]:
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise IFindHttpError(IFindHttpErrorCategory.RESPONSE_SCHEMA_ERROR, "iFinD returned non-JSON authentication response") from exc
    if not isinstance(payload, dict):
        raise IFindHttpError(IFindHttpErrorCategory.RESPONSE_SCHEMA_ERROR, "iFinD authentication response must be an object")
    return payload


def _find_value(payload: dict[str, Any], key: str) -> Any:
    for candidate_key, value in payload.items():
        if candidate_key.lower() == key.lower():
            return value
        if isinstance(value, dict):
            found = _find_value(value, key)
            if found is not None:
                return found
    return None


def _provider_code(payload: dict[str, Any]) -> Any:
    for name in ("errorcode", "error_code", "code"):
        value = _find_value(payload, name)
        if value is not None:
            return value
    return None


def _provider_message(payload: dict[str, Any]) -> str:
    for name in ("errmsg", "error_message", "message", "msg"):
        value = _find_value(payload, name)
        if value is not None:
            return str(value)[:160]
    return ""


def _provider_success(code: Any) -> bool:
    return code is None or str(code).strip().lower() in {"0", "success", "ok"}


def _expires_at(payload: dict[str, Any]) -> datetime | None:
    expires_in = _find_value(payload, "expires_in")
    try:
        return datetime.now(timezone.utc) + timedelta(seconds=max(0, int(expires_in))) if expires_in is not None else None
    except (TypeError, ValueError):
        return None


def response_schema_hash(payload: Any) -> str:
    shape = _shape(payload)
    return hashlib.sha256(json.dumps(shape, ensure_ascii=True, sort_keys=True).encode("utf-8")).hexdigest()


def _shape(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _shape(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_shape(value[0])] if value else []
    return type(value).__name__
