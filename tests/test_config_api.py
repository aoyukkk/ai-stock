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
    for forbidden in (
        "api_key",
        "password",
        "secret",
        "credential",
        "openai_api_key",
        "deepseek_api_key",
    ):
        assert forbidden not in text


def test_get_effective_config_returns_safe_unified_response() -> None:
    response = client.get("/api/v1/config/effective")

    assert response.status_code == 200
    payload = response.json()
    assert_success(payload)
    assert_no_sensitive(payload)
    assert payload["data"]["real_trading_enabled"] is False
    assert payload["data"]["llm_mock_only"] is True
    assert payload["data"]["data_source_mode"] == "mock_only"
    assert payload["data"]["values"]["paper_trading.real_trading_enabled"] is False


def test_get_editable_config_returns_whitelisted_items() -> None:
    response = client.get("/api/v1/config/editable")

    assert response.status_code == 200
    payload = response.json()
    assert_success(payload)
    assert_no_sensitive(payload)

    keys = {item["config_key"] for item in payload["data"]["items"]}
    assert "stock_scan.quant_top_n" in keys
    assert "llm.mock_only" in keys
    assert "paper_trading.real_trading_enabled" not in keys
    assert "OPENAI_API_KEY" not in keys


def test_update_reset_and_history_flow() -> None:
    key = "order_price.atr_window"

    update = client.put(
        f"/api/v1/config/values/{key}",
        json={"value": 21, "user": "api_tester", "reason": "api update"},
    )
    assert update.status_code == 200
    update_payload = update.json()
    assert_success(update_payload)
    assert update_payload["data"]["config_key"] == key
    assert update_payload["data"]["effective_value"] == 21

    history = client.get("/api/v1/config/history", params={"config_key": key, "limit": 5})
    assert history.status_code == 200
    history_payload = history.json()
    assert_success(history_payload)
    assert history_payload["data"]["items"]
    assert history_payload["data"]["items"][0]["config_key"] == key

    reset = client.post(
        f"/api/v1/config/values/{key}/reset",
        json={"user": "api_tester", "reason": "api reset"},
    )
    assert reset.status_code == 200
    reset_payload = reset.json()
    assert_success(reset_payload)
    assert reset_payload["data"]["effective_value"] == 14


def test_bulk_update_persists_whitelisted_config() -> None:
    response = client.post(
        "/api/v1/config/bulk",
        json={
            "items": [
                {"config_key": "order_price.min_risk_reward", "value": 1.6},
                {"config_key": "order_price.ideal_risk_reward", "value": 2.1},
            ],
            "user": "api_tester",
            "reason": "bulk api update",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert_success(payload)
    assert len(payload["data"]["items"]) == 2

    for key in ("order_price.min_risk_reward", "order_price.ideal_risk_reward"):
        reset = client.post(f"/api/v1/config/values/{key}/reset")
        assert reset.status_code == 200


def test_blocked_config_keys_return_safety_error() -> None:
    for key, value in (
        ("OPENAI_API_KEY", "unsafe"),
        ("paper_trading.real_trading_enabled", True),
        ("llm.default_provider", "openai"),
    ):
        response = client.put(
            f"/api/v1/config/values/{key}",
            json={"value": value, "user": "api_tester", "reason": "blocked"},
        )
        assert response.status_code == 400
        payload = response.json()
        assert payload["success"] is False
        assert payload["trace_id"]
        assert_no_sensitive(payload)
        assert payload["code"] in {"CONFIG_KEY_NOT_EDITABLE", "CONFIG_VALUE_INVALID"}
