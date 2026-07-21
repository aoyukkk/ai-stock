from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from datasource.ifind.http.auth import IFindHttpAuthManager
from datasource.ifind.http.client import IFindHttpClient
from datasource.ifind.http.endpoints import ENDPOINTS, endpoint
from datasource.ifind.http.errors import IFindHttpError, IFindHttpErrorCategory
from datasource.ifind.http.normalizer import normalize_probe_response
from datasource.ifind.http.provider import IFindHttpP0Provider
from datasource.ifind.http.rate_limiter import IFindHttpSerialRateLimiter
from datasource.ifind.http.schemas import IFindHttpResponse
from datasource.ifind.transport import IFindCredentialStatus, IFindTransportMode, select_transport


class FakeResponse:
    def __init__(self, status_code: int, payload) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, responses, calls, **_kwargs) -> None:
        self.responses = responses
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def post(self, url, *, headers, json):
        self.calls.append({"url": url, "headers": dict(headers), "json": json})
        return self.responses.pop(0)


def factory(responses, calls):
    return lambda **kwargs: FakeClient(responses, calls, **kwargs)


def test_auto_transport_prefers_http_refresh_over_sdk(monkeypatch) -> None:
    monkeypatch.setenv("IFIND_REFRESH_TOKEN", "refresh-test-value")
    monkeypatch.setenv("IFIND_ACCESS_TOKEN", "access-test-value")
    monkeypatch.setenv("IFIND_USERNAME", "user")
    monkeypatch.setenv("IFIND_PASSWORD", "password-test")
    assert select_transport("auto") == (IFindTransportMode.HTTP, IFindCredentialStatus.CONFIGURED)
    assert select_transport("sdk") == (IFindTransportMode.SDK, IFindCredentialStatus.CONFIGURED)


def test_access_only_selects_short_lived_http(monkeypatch) -> None:
    monkeypatch.delenv("IFIND_REFRESH_TOKEN", raising=False)
    monkeypatch.setenv("IFIND_ACCESS_TOKEN", "access-test-value")
    monkeypatch.delenv("IFIND_USERNAME", raising=False)
    monkeypatch.delenv("IFIND_PASSWORD", raising=False)
    assert select_transport() == (IFindTransportMode.HTTP, IFindCredentialStatus.ACCESS_TOKEN_ONLY_NO_REFRESH)


def test_sdk_installation_does_not_change_transport_selection(monkeypatch) -> None:
    monkeypatch.delenv("IFIND_REFRESH_TOKEN", raising=False)
    monkeypatch.delenv("IFIND_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("IFIND_USERNAME", raising=False)
    monkeypatch.delenv("IFIND_PASSWORD", raising=False)
    assert select_transport("auto") == (IFindTransportMode.DISABLED, IFindCredentialStatus.NOT_CONFIGURED)


def test_refresh_token_auth_uses_header_and_keeps_access_in_memory() -> None:
    responses = [FakeResponse(200, {"errorcode": 0, "data": {"access_token": "new-access-token", "expires_in": 3600}})]
    calls = []
    auth = IFindHttpAuthManager(
        base_url="https://official.example/api/v1", refresh_token="refresh-test-value",
        client_factory=factory(responses, calls),
    )
    result = auth.refresh_access_token()
    assert result.status == "CONFIGURED"
    assert auth.access_token() == "new-access-token"
    assert auth.auth_calls == 1
    assert calls[0]["headers"]["refresh_token"] == "refresh-test-value"
    assert "refresh_token" not in calls[0]["url"]


@pytest.mark.parametrize(("payload", "expected"), [
    ({"errorcode": -1, "errmsg": "refresh token invalid"}, IFindHttpErrorCategory.REFRESH_TOKEN_INVALID),
    ({"errorcode": -1, "errmsg": "device limit exceeded"}, IFindHttpErrorCategory.DEVICE_LIMIT_EXCEEDED),
])
def test_auth_errors_are_classified_without_secret(payload, expected) -> None:
    auth = IFindHttpAuthManager(
        base_url="https://official.example/api/v1", refresh_token="refresh-test-value",
        client_factory=factory([FakeResponse(401, payload)], []),
    )
    with pytest.raises(IFindHttpError) as captured:
        auth.refresh_access_token()
    assert captured.value.category == expected
    assert "refresh-test-value" not in str(captured.value)


def test_access_token_failure_refreshes_once_then_retries() -> None:
    auth_calls = []
    auth = IFindHttpAuthManager(
        base_url="https://official.example/api/v1", refresh_token="refresh-test-value", access_token="old-access-token",
        client_factory=factory([FakeResponse(200, {"errorcode": 0, "data": {"access_token": "new-access-token"}})], auth_calls),
    )
    data_calls = []
    client = IFindHttpClient(
        auth, maximum_calls=2, interval_ms=1000, sleep=lambda _seconds: None,
        client_factory=factory([
            FakeResponse(401, {"errorcode": -1, "errmsg": "access token expired"}),
            FakeResponse(200, {"errorcode": 0, "data": [{"thscode": "000001.SH", "latest": 1.0}]}),
        ], data_calls),
    )
    response = client.post("real_time_quotation", {"codes": "000001.SH", "indicators": "latest"})
    assert response.status_code == 200
    assert client.call_count == 2
    assert auth.auth_calls == 1
    assert data_calls[-1]["headers"]["access_token"] == "new-access-token"


def test_endpoint_registry_is_centralized_and_complete() -> None:
    expected = {
        "get_access_token", "real_time_quotation", "high_frequency", "cmd_history_quotation",
        "basic_data_service", "date_sequence", "data_pool", "report_query", "edb_service",
        "snap_shot", "get_trade_dates",
    }
    assert set(ENDPOINTS) == expected
    assert endpoint("get_access_token").path == "/get_access_token"
    source = Path("datasource/ifind/http/endpoints.py").read_text(encoding="utf-8")
    assert "quantapi.51ifind.com" not in source


def test_http_limiter_rejects_more_than_thirty_calls() -> None:
    with pytest.raises(ValueError, match="CALL_LIMIT"):
        IFindHttpSerialRateLimiter(maximum=31)


def test_response_normalization_handles_codes_decimal_nan_and_closed_session() -> None:
    summary = normalize_probe_response(
        {"data": [{"thscode": "000001.SH", "latest": float("nan"), "time": "2026-07-14 15:00:00"}]},
        observed_at=datetime(2026, 7, 14, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    assert summary.row_count == 1
    assert summary.returned_fields == ("latest", "thscode", "time")
    assert summary.provider_timestamp == "2026-07-14 15:00:00"
    assert summary.data_status == "CLOSED_SESSION_FINAL"


def test_response_normalization_expands_ifind_columnar_tables() -> None:
    summary = normalize_probe_response({
        "tables": [{
            "thscode": "000001.SH",
            "time": ["2026-07-14 14:59:00", "2026-07-14 15:00:00"],
            "table": {"open": [10.0, 10.1], "close": [10.1, 10.2]},
        }],
    })
    assert summary.row_count == 2
    assert set(summary.returned_fields) == {"thscode", "time", "open", "close"}
    assert summary.provider_timestamp == "2026-07-14 15:00:00"


def test_previous_trade_date_is_not_marked_as_current_close_final() -> None:
    summary = normalize_probe_response(
        {"data": [{"thscode": "000001.SH", "latest": 10.2, "time": "2026-07-14 15:00:00"}]},
        observed_at=datetime(2026, 7, 15, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    assert summary.data_status == "AVAILABLE_DELAYED"


def test_tokens_do_not_appear_in_serialized_auth_result() -> None:
    auth = IFindHttpAuthManager(
        base_url="https://official.example/api/v1", refresh_token="refresh-test-value", access_token="access-test-value",
    )
    serialized = json.dumps(auth.last_result.__dict__)
    assert "refresh-test-value" not in serialized
    assert "access-test-value" not in serialized


class FakeP0Client:
    def __init__(self) -> None:
        self.calls = []

    def post(self, endpoint_name, payload):
        self.calls.append((endpoint_name, payload))
        realtime = endpoint_name == "real_time_quotation"
        fields = {
            "open": [10.0], "high": [10.5], "low": [9.8],
            "volume": [1000], "amount": [10000],
        }
        fields["latest" if realtime else "close"] = [10.2]
        return IFindHttpResponse(
            endpoint=endpoint_name, status_code=200, latency_ms=1, provider_code=0,
            provider_message="", schema_hash="schema-test",
            payload={"tables": [{"thscode": "000001.SH", "time": ["2026-07-14 15:00:00"], "table": fields}]},
        )


def test_verified_p0_provider_is_disabled_by_default() -> None:
    provider = IFindHttpP0Provider(FakeP0Client())
    with pytest.raises(RuntimeError, match="PROVIDER_DISABLED"):
        provider.stock_realtime(["000001.SH"])


def test_verified_p0_provider_normalizes_typed_results_and_caches() -> None:
    client = FakeP0Client()
    provider = IFindHttpP0Provider(client, enabled=True, cache_ttl_seconds=30)
    first = provider.stock_realtime(["000001.SH"])
    second = provider.stock_realtime(["000001.SH"])
    assert first == second
    assert len(client.calls) == 1
    assert first[0].stock_code == "000001.SH"
    assert first[0].latest == 10.2
    assert first[0].provider == "IFIND_HTTP"
    assert first[0].data_status == "AVAILABLE_DELAYED"
    assert "payload" not in first[0].model_dump()
