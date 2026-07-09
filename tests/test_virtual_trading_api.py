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


def test_virtual_trading_api_flow() -> None:
    account_response = client.post("/api/v1/virtual-trading/accounts/default")
    assert account_response.status_code == 200
    account_payload = account_response.json()
    assert_success(account_payload)
    assert_no_sensitive(account_payload)
    assert account_payload["data"]["real_trading_enabled"] is False

    run_response = client.post("/api/v1/virtual-trading/run-plans?input_top_n=2")
    assert run_response.status_code == 200
    run_payload = run_response.json()
    assert_success(run_payload)
    assert_no_sensitive(run_payload)
    assert run_payload["data"]["real_trading_enabled"] is False
    assert run_payload["data"]["summary"]["virtual_only"] is True

    for path, key in (
        ("/api/v1/virtual-trading/account", "account_id"),
        ("/api/v1/virtual-trading/orders", "orders"),
        ("/api/v1/virtual-trading/positions", "positions"),
        ("/api/v1/virtual-trading/trades", "trades"),
    ):
        response = client.get(path)
        assert response.status_code == 200
        payload = response.json()
        assert_success(payload)
        assert_no_sensitive(payload)
        assert key in payload["data"]
        assert payload["data"]["real_trading_enabled"] is False


def test_virtual_trading_cancel_and_reprice_api_available() -> None:
    client.post("/api/v1/virtual-trading/run-plans?input_top_n=2")
    orders_payload = client.get("/api/v1/virtual-trading/orders").json()
    orders = orders_payload["data"]["orders"]
    assert orders
    target = next((order for order in orders if order["status"] == "PENDING"), orders[0])

    cancel_response = client.post(f"/api/v1/virtual-trading/orders/{target['order_id']}/cancel?reason=api-test")
    assert cancel_response.status_code == 200
    assert_success(cancel_response.json())

    reprice_response = client.post(
        f"/api/v1/virtual-trading/orders/{target['order_id']}/reprice",
        json={"new_price": "7.50", "reason": "api-test"},
    )
    assert reprice_response.status_code == 200
    assert_success(reprice_response.json())
