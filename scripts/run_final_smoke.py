from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

ROOT_BOOTSTRAP = Path(__file__).resolve().parents[1]
if str(ROOT_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(ROOT_BOOTSTRAP))

from fastapi.testclient import TestClient

from backend.main import create_app


HttpMethod = Literal["GET", "POST"]


SAFE_TOKEN_KEYS = {
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "total_tokens",
    "daily_token_budget",
}
SECRET_VALUE_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
SENSITIVE_KEY_PARTS = ("api_key", "password", "secret", "credential", "username")


@dataclass(frozen=True)
class SmokeEndpoint:
    name: str
    method: HttpMethod
    path: str
    params: dict[str, Any] | None = None
    json_body: dict[str, Any] | None = None


SMOKE_ENDPOINTS: tuple[SmokeEndpoint, ...] = (
    SmokeEndpoint("health", "GET", "/health"),
    SmokeEndpoint("system config summary", "GET", "/api/v1/system/config-summary"),
    SmokeEndpoint("database health", "GET", "/api/v1/database/health"),
    SmokeEndpoint("data source status", "GET", "/api/v1/data-sources/status"),
    SmokeEndpoint("llm status", "GET", "/api/v1/llm/status"),
    SmokeEndpoint("quant scan", "GET", "/api/v1/quant/scan", params={"top_q": 8}),
    SmokeEndpoint(
        "light screening",
        "GET",
        "/api/v1/screening/light/run",
        params={"quant_top_q": 8, "top_n": 5},
    ),
    SmokeEndpoint(
        "committee",
        "GET",
        "/api/v1/committee/run",
        params={"input_top_n": 5, "final_top_n": 3},
    ),
    SmokeEndpoint(
        "order price",
        "GET",
        "/api/v1/order-price/plans",
        params={"input_top_n": 2},
    ),
    SmokeEndpoint("virtual account", "POST", "/api/v1/virtual-trading/accounts/default"),
    SmokeEndpoint(
        "virtual run",
        "POST",
        "/api/v1/virtual-trading/run-plans",
        params={"input_top_n": 2},
    ),
    SmokeEndpoint("alerts", "POST", "/api/v1/alerts/intraday/scan"),
    SmokeEndpoint(
        "pre-market recheck",
        "POST",
        "/api/v1/recheck/pre-market/run",
        params={"limit": 3},
    ),
    SmokeEndpoint(
        "daily review",
        "POST",
        "/api/v1/review/daily/run",
        json_body={"date": "2026-01-05", "use_mock_llm": False},
    ),
    SmokeEndpoint(
        "memory search",
        "POST",
        "/api/v1/memory/search",
        json_body={"stock_code": "000001", "top_k": 5},
    ),
    SmokeEndpoint("effective config", "GET", "/api/v1/config/effective"),
)


def run_smoke(client: TestClient | None = None) -> list[dict[str, Any]]:
    active_client = client or TestClient(create_app())
    results: list[dict[str, Any]] = []
    for endpoint in SMOKE_ENDPOINTS:
        response = active_client.request(
            endpoint.method,
            endpoint.path,
            params=endpoint.params,
            json=endpoint.json_body,
        )
        result = _validate_response(endpoint, response)
        results.append(result)
    return results


def _validate_response(endpoint: SmokeEndpoint, response) -> dict[str, Any]:
    trace_id = response.headers.get("X-Trace-Id")
    payload: dict[str, Any] | None = None
    errors: list[str] = []

    try:
        payload = response.json()
    except ValueError:
        errors.append("response is not JSON")

    if response.status_code != 200:
        errors.append(f"status code {response.status_code}")

    if payload is not None:
        for key in ("success", "code", "message", "data", "trace_id"):
            if key not in payload:
                errors.append(f"missing envelope key: {key}")
        if payload.get("success") is not True:
            errors.append("success is not true")
        if payload.get("code") != "OK":
            errors.append("code is not OK")
        if not payload.get("trace_id"):
            errors.append("trace_id missing")
        if _contains_sensitive(payload):
            errors.append("sensitive field exposed")
        if _find_true_real_trading(payload):
            errors.append("real_trading_enabled true")

    return {
        "passed": not errors,
        "name": endpoint.name,
        "method": endpoint.method,
        "path": endpoint.path,
        "status_code": response.status_code,
        "trace_id": (payload or {}).get("trace_id") or trace_id,
        "errors": errors,
    }


def _contains_sensitive(value: Any, parent_key: str = "") -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower()
            if normalized not in SAFE_TOKEN_KEYS:
                if (
                    "token" in normalized
                    and not normalized.endswith("_tokens")
                    and "token_budget" not in normalized
                ):
                    return True
                if any(part in normalized for part in SENSITIVE_KEY_PARTS):
                    return True
            if _contains_sensitive(child, normalized):
                return True
    elif isinstance(value, list):
        return any(_contains_sensitive(item, parent_key) for item in value)
    elif isinstance(value, str):
        if any(pattern.search(value) for pattern in SECRET_VALUE_PATTERNS):
            return True
    return False


def _find_true_real_trading(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() == "real_trading_enabled" and child is True:
                return True
            if _find_true_real_trading(child):
                return True
    elif isinstance(value, list):
        return any(_find_true_real_trading(item) for item in value)
    return False


def main() -> int:
    results = run_smoke()
    for result in results:
        status = "PASS" if result["passed"] else "FAIL"
        print(
            f"{status} {result['method']} {result['path']} "
            f"status={result['status_code']} trace_id={result.get('trace_id') or '-'}"
        )
        for error in result["errors"]:
            print(f"  - {error}")

    passed = sum(1 for result in results if result["passed"])
    total = len(results)
    print(json.dumps({"passed": passed, "total": total}, ensure_ascii=False))
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
