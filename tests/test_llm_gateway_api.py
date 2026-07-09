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
    for forbidden in ["api_key", "password", "secret", "username", "deepseek_api_key"]:
        assert forbidden not in text


def test_llm_status_api() -> None:
    response = client.get("/api/v1/llm/status")

    assert response.status_code == 200
    payload = response.json()
    assert_success(payload)
    assert_no_sensitive(payload)
    assert payload["data"]["mock_only"] is True
    assert payload["data"]["default_provider"] == "mock"
    assert payload["data"]["real_trading_enabled"] is False
    assert any(provider["name"] == "mock" for provider in payload["data"]["providers"])


def test_llm_mock_chat_api() -> None:
    response = client.post(
        "/api/v1/llm/mock-chat",
        json={
            "agent_name": "technical_agent",
            "task": "test",
            "message": "hello",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert_success(payload)
    assert_no_sensitive(payload)
    data = payload["data"]
    assert data["provider"] == "mock"
    assert data["model"]
    assert data["content"]
    assert data["input_tokens"] >= 0
    assert data["output_tokens"] >= 0
    assert data["total_tokens"] >= data["input_tokens"]
    assert data["cost_usd"] == 0
    assert data["request_hash"]


def test_llm_usage_summary_api() -> None:
    client.post(
        "/api/v1/llm/mock-chat",
        json={
            "agent_name": "technical_agent",
            "task": "test",
            "message": "usage summary",
        },
    )
    response = client.get("/api/v1/llm/usage-summary")

    assert response.status_code == 200
    payload = response.json()
    assert_success(payload)
    assert_no_sensitive(payload)
    assert payload["data"]["call_count"] >= 1
    assert payload["data"]["cost_usd"] == 0
