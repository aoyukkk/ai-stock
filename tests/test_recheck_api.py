import json

from fastapi.testclient import TestClient

from backend.main import create_app


client = TestClient(create_app())


def assert_no_sensitive(payload: dict) -> None:
    text = json.dumps(payload, ensure_ascii=False).lower()
    for forbidden in ("api_key", "password", "secret", "token", "username"):
        assert forbidden not in text


def test_pre_market_run_api_returns_unified_response() -> None:
    response = client.post("/api/v1/recheck/pre-market/run?limit=5")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["trace_id"]
    assert "results" in payload["data"]
    assert payload["data"]["real_trading_enabled"] is False
    assert_no_sensitive(payload)


def test_single_order_plan_recheck_api_returns_reasonable_error_for_missing_plan() -> None:
    response = client.post("/api/v1/recheck/order-plans/999999999")

    assert response.status_code == 404
    payload = response.json()
    assert payload["success"] is False
    assert payload["code"] == "ORDER_PLAN_NOT_FOUND"
    assert payload["trace_id"]
    assert_no_sensitive(payload)


def test_recheck_config_api_no_sensitive_fields() -> None:
    response = client.get("/api/v1/recheck/config")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["advisory_only"] is True
    assert payload["data"]["real_trading_enabled"] is False
    assert_no_sensitive(payload)
