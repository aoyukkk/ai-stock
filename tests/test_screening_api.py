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


def test_light_screening_run_api() -> None:
    response = client.get("/api/v1/screening/light/run?quant_top_q=8&top_n=5")

    assert response.status_code == 200
    payload = response.json()
    assert_success(payload)
    assert_no_sensitive(payload)
    data = payload["data"]
    assert data["requested_quant_top_q"] == 8
    assert data["requested_top_n"] == 5
    assert data["returned_count"] <= 5
    assert data["results"]
    assert data["real_trading_enabled"] is False


def test_light_screening_config_api() -> None:
    response = client.get("/api/v1/screening/light/config")

    assert response.status_code == 200
    payload = response.json()
    assert_success(payload)
    assert_no_sensitive(payload)
    assert payload["data"]["provider"] == "mock"
    assert payload["data"]["model"] == "mock-chat"
    assert payload["data"]["real_trading_enabled"] is False
