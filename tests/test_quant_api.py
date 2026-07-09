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
    for forbidden in ("api_key", "password", "secret", "token"):
        assert forbidden not in text


def test_quant_scan_api() -> None:
    response = client.get("/api/v1/quant/scan?top_q=5")

    assert response.status_code == 200
    payload = response.json()
    assert_success(payload)
    assert_no_sensitive(payload)
    data = payload["data"]
    assert data["requested_top_q"] == 5
    assert data["returned_count"] == 5
    assert data["results"][0]["rank"] == 1
    assert data["real_trading_enabled"] is False


def test_quant_config_api() -> None:
    response = client.get("/api/v1/quant/config")

    assert response.status_code == 200
    payload = response.json()
    assert_success(payload)
    assert_no_sensitive(payload)
    assert payload["data"]["factor_version"] == "v0.3-phase4"
    assert abs(sum(payload["data"]["weights"].values()) - 1.0) < 0.0001
