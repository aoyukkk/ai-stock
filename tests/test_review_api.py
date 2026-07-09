import json

from fastapi.testclient import TestClient

from backend.main import create_app


client = TestClient(create_app())


def assert_no_sensitive(payload: dict) -> None:
    text = json.dumps(payload, ensure_ascii=False).lower()
    for forbidden in ("api_key", "password", "secret", "token", "username"):
        assert forbidden not in text


def test_review_daily_run_api_returns_unified_response() -> None:
    response = client.post(
        "/api/v1/review/daily/run",
        json={"date": "2026-01-05", "use_mock_llm": False},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["trace_id"]
    assert payload["data"]["real_trading_enabled"] is False
    assert "final_review_score" in payload["data"]
    assert_no_sensitive(payload)


def test_review_config_api_returns_safe_summary() -> None:
    response = client.get("/api/v1/review/config")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["llm_mode"] == "mock_only"
    assert payload["data"]["real_trading_enabled"] is False
    assert_no_sensitive(payload)


def test_review_prediction_and_order_plan_evaluate_apis_are_available() -> None:
    prediction_response = client.post(
        "/api/v1/review/predictions/evaluate",
        json={"date": "2026-01-05"},
    )
    order_response = client.post(
        "/api/v1/review/order-plans/evaluate",
        json={"date": "2026-01-05"},
    )

    assert prediction_response.status_code == 200
    assert order_response.status_code == 200
    assert prediction_response.json()["data"]["real_trading_enabled"] is False
    assert order_response.json()["data"]["real_trading_enabled"] is False
    assert_no_sensitive(prediction_response.json())
    assert_no_sensitive(order_response.json())


def test_review_daily_get_api_returns_persisted_review() -> None:
    client.post(
        "/api/v1/review/daily/run",
        json={"date": "2026-01-05", "use_mock_llm": False},
    )
    response = client.get("/api/v1/review/daily/2026-01-05")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["date"] == "2026-01-05"
    assert payload["data"]["real_trading_enabled"] is False
    assert_no_sensitive(payload)
