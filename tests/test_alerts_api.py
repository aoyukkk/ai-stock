import json

from fastapi.testclient import TestClient

from backend.main import create_app


client = TestClient(create_app())


def assert_no_sensitive(payload: dict) -> None:
    text = json.dumps(payload, ensure_ascii=False).lower()
    for forbidden in ("api_key", "password", "secret", "token", "username"):
        assert forbidden not in text


def test_intraday_scan_api_returns_unified_response() -> None:
    response = client.post("/api/v1/alerts/intraday/scan")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert "alerts" in payload["data"]
    assert payload["data"]["real_trading_enabled"] is False
    assert_no_sensitive(payload)


def test_recent_alerts_api_returns_unified_response() -> None:
    client.post("/api/v1/alerts/intraday/scan")
    response = client.get("/api/v1/alerts/recent")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert "alerts" in payload["data"]
    assert payload["data"]["real_trading_enabled"] is False
    assert_no_sensitive(payload)


def test_alerts_config_api_no_sensitive_fields() -> None:
    response = client.get("/api/v1/alerts/config")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["advisory_only"] is True
    assert payload["data"]["real_trading_enabled"] is False
    assert_no_sensitive(payload)
