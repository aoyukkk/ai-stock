import json

from fastapi.testclient import TestClient

from backend.main import create_app


client = TestClient(create_app())


def test_config_summary_returns_safe_summary() -> None:
    response = client.get("/api/v1/system/config-summary")

    assert response.status_code == 200
    assert response.headers["X-Trace-Id"]

    payload = response.json()
    assert payload["success"] is True
    assert payload["code"] == "OK"
    assert payload["trace_id"] == response.headers["X-Trace-Id"]

    data = payload["data"]
    assert data["system"]["name"] == "AI Trader Assistant"
    assert data["system"]["real_trading_enabled"] is False
    assert data["config_priority"] == [
        "Web UI",
        "Database",
        "Config File",
        "Default",
    ]
    assert data["stock_scan"]["quant_top_n"] == 500
    assert data["stock_scan"]["light_analysis_top_n"] == 500
    assert data["stock_scan"]["committee_analysis_top_n"] == 50
    assert data["stock_scan"]["final_recommend_top_n"] == 50
    assert data["llm"]["mock_enabled"] is True
    assert data["llm"]["real_providers_enabled"] == []
    assert data["data_sources"]["mock_enabled"] is True
    assert data["data_sources"]["real_sources_enabled"] == []
    assert data["virtual_trading"]["enabled"] is True
    assert data["virtual_trading"]["real_trading_enabled"] is False


def test_config_summary_does_not_expose_sensitive_values() -> None:
    response = client.get("/api/v1/system/config-summary")
    payload_text = json.dumps(response.json(), ensure_ascii=False).lower()

    forbidden_terms = [
        "api_key",
        "deepseek_api_key",
        "openai_api_key",
        "password",
        "secret",
        "token",
        "username",
        "credential",
        "ifind_username",
        "ifind_password",
    ]
    for forbidden_term in forbidden_terms:
        assert forbidden_term not in payload_text
