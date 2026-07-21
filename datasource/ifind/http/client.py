from __future__ import annotations

import json
import time
from typing import Any, Callable

import httpx

from datasource.ifind.http.auth import IFindHttpAuthManager, _provider_code, _provider_message, _provider_success, response_schema_hash
from datasource.ifind.http.endpoints import endpoint
from datasource.ifind.http.errors import IFindHttpError, IFindHttpErrorCategory, classify_http_error
from datasource.ifind.http.rate_limiter import IFindHttpSerialRateLimiter
from datasource.ifind.http.schemas import IFindHttpResponse


class IFindHttpClient:
    def __init__(
        self,
        auth: IFindHttpAuthManager,
        *,
        timeout_seconds: float = 30,
        maximum_calls: int = 30,
        authorized_maximum_calls: int = 30,
        interval_ms: int = 1000,
        client_factory: Callable[..., Any] = httpx.Client,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.auth = auth
        self.timeout_seconds = timeout_seconds
        self.client_factory = client_factory
        self.limiter = IFindHttpSerialRateLimiter(
            maximum_calls,
            interval_ms,
            authorized_maximum=authorized_maximum_calls,
            sleep=sleep,
        )
        self.success_count = 0
        self.failed_count = 0
        self.unauthorized_count = 0

    @property
    def call_count(self) -> int:
        return self.limiter.call_count

    def post(self, endpoint_name: str, payload: dict[str, Any]) -> IFindHttpResponse:
        try:
            return self._post_once(endpoint_name, payload)
        except IFindHttpError as exc:
            if exc.category in {IFindHttpErrorCategory.ACCESS_TOKEN_INVALID, IFindHttpErrorCategory.ACCESS_TOKEN_EXPIRED} and self.auth.has_refresh_token:
                self.auth.refresh_access_token()
                return self._post_once(endpoint_name, payload)
            raise

    def _post_once(self, endpoint_name: str, payload: dict[str, Any]) -> IFindHttpResponse:
        target = endpoint(endpoint_name)
        if target.authentication != "access_token":
            raise ValueError("IFIND_HTTP_DATA_ENDPOINT_REQUIRED")
        self.limiter.wait()
        self.limiter.record()
        started = time.perf_counter()
        try:
            with self.client_factory(timeout=self.timeout_seconds) as client:
                response = client.post(
                    f"{self.auth.base_url}{target.path}",
                    headers={"Content-Type": "application/json", "access_token": self.auth.access_token()},
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            self.failed_count += 1
            raise IFindHttpError(IFindHttpErrorCategory.TIMEOUT, f"iFinD {endpoint_name} timed out") from exc
        except httpx.HTTPError as exc:
            self.failed_count += 1
            raise IFindHttpError(IFindHttpErrorCategory.NETWORK_ERROR, f"iFinD {endpoint_name} network error") from exc

        latency_ms = round((time.perf_counter() - started) * 1000)
        body = _json_mapping(response, endpoint_name)
        provider_code = _provider_code(body)
        provider_message = _provider_message(body)
        if response.status_code >= 400 or not _provider_success(provider_code):
            category = classify_http_error(response.status_code, provider_code, provider_message, authentication=False)
            self.failed_count += 1
            if category in {IFindHttpErrorCategory.ACCESS_TOKEN_INVALID, IFindHttpErrorCategory.ACCESS_TOKEN_EXPIRED, IFindHttpErrorCategory.NOT_AUTHORIZED}:
                self.unauthorized_count += 1
            raise IFindHttpError(category, f"iFinD {endpoint_name} failed ({category.value})", status_code=response.status_code)
        self.success_count += 1
        return IFindHttpResponse(
            endpoint=endpoint_name, status_code=response.status_code, latency_ms=latency_ms,
            provider_code=provider_code, provider_message=provider_message,
            schema_hash=response_schema_hash(body), payload=body,
        )


def _json_mapping(response: Any, endpoint_name: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise IFindHttpError(IFindHttpErrorCategory.RESPONSE_SCHEMA_ERROR, f"iFinD {endpoint_name} returned non-JSON data") from exc
    if not isinstance(payload, dict):
        raise IFindHttpError(IFindHttpErrorCategory.RESPONSE_SCHEMA_ERROR, f"iFinD {endpoint_name} response must be an object")
    return payload
