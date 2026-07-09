import json

from fastapi.testclient import TestClient

from backend.main import create_app


client = TestClient(create_app())


def assert_success(payload: dict) -> None:
    assert payload["success"] is True
    assert payload["code"] == "OK"
    assert payload["trace_id"]


def assert_no_sensitive(payload: dict) -> None:
    text = json.dumps(payload, ensure_ascii=False).lower()
    for forbidden in ("api_key", "password", "secret", "token", "username"):
        assert forbidden not in text


def test_order_price_plans_api() -> None:
    response = client.get("/api/v1/order-price/plans?input_top_n=2")

    assert response.status_code == 200
    payload = response.json()
    assert_success(payload)
    assert_no_sensitive(payload)
    data = payload["data"]
    assert data["returned_count"] >= 1
    assert data["plans"]
    plan = data["plans"][0]
    assert plan["recommended_price"] <= plan["max_acceptable_price"]
    assert plan["candidates"]
    assert data["real_trading_enabled"] is False


def test_order_price_config_api() -> None:
    response = client.get("/api/v1/order-price/config")

    assert response.status_code == 200
    payload = response.json()
    assert_success(payload)
    assert_no_sensitive(payload)
    data = payload["data"]
    assert data["enabled"] is True
    assert data["llm_can_generate_price"] is False
    assert data["requires_rule_engine"] is True
    assert data["real_trading_enabled"] is False
