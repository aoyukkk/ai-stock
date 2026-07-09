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


def test_committee_run_api() -> None:
    response = client.get("/api/v1/committee/run?input_top_n=3&final_top_n=2")

    assert response.status_code == 200
    payload = response.json()
    assert_success(payload)
    assert_no_sensitive(payload)
    data = payload["data"]
    assert data["input_count"] >= 1
    assert data["requested_top_n"] == 2
    assert data["returned_count"] <= 2
    assert data["results"]
    first = data["results"][0]
    assert first["stock_code"]
    assert first["final_score"] is not None
    assert first["recommendation"] in {"STRONG_WATCH", "WATCH", "NEUTRAL", "AVOID", "BLOCKED"}
    assert first["risk_level"] in {"LOW", "MEDIUM", "HIGH", "BLACK_SWAN"}
    assert data["real_trading_enabled"] is False


def test_committee_config_api() -> None:
    response = client.get("/api/v1/committee/config")

    assert response.status_code == 200
    payload = response.json()
    assert_success(payload)
    assert_no_sensitive(payload)
    data = payload["data"]
    assert data["mock_only"] is True
    assert data["real_trading_enabled"] is False
    assert "technical_agent" in data["enabled_agents"]
    assert sum(data["weights"].values()) == 1.0
