import json

from fastapi.testclient import TestClient

from backend.main import create_app


client = TestClient(create_app())


def assert_unified_success(payload: dict) -> None:
    assert payload["success"] is True
    assert payload["code"] == "OK"
    assert payload["message"] == "ok"
    assert "data" in payload
    assert payload["trace_id"]


def assert_no_sensitive_values(payload: dict) -> None:
    payload_text = json.dumps(payload, ensure_ascii=False).lower()
    for forbidden in ("api_key", "password", "secret", "token", "username"):
        assert forbidden not in payload_text


def test_data_sources_status_api() -> None:
    response = client.get("/api/v1/data-sources/status")

    assert response.status_code == 200
    assert response.headers["X-Trace-Id"]
    payload = response.json()
    assert_unified_success(payload)
    assert_no_sensitive_values(payload)

    providers = payload["data"]["providers"]
    assert any(provider["name"] == "mock" and provider["is_mock"] for provider in providers)
    assert payload["data"]["real_trading_enabled"] is False


def test_mock_stocks_api() -> None:
    response = client.get("/api/v1/data-sources/mock/stocks")

    assert response.status_code == 200
    payload = response.json()
    assert_unified_success(payload)
    assert_no_sensitive_values(payload)
    assert payload["data"]["count"] >= 20
    assert payload["data"]["stocks"][0]["stock_code"]


def test_mock_quotes_api() -> None:
    response = client.get("/api/v1/data-sources/mock/quotes?stock_codes=000001,600519")

    assert response.status_code == 200
    payload = response.json()
    assert_unified_success(payload)
    assert_no_sensitive_values(payload)
    assert payload["data"]["count"] == 2
    assert [quote["stock_code"] for quote in payload["data"]["quotes"]] == ["000001", "600519"]
