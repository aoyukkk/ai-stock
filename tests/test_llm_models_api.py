import json

from fastapi.testclient import TestClient

from backend.main import create_app
from llm_gateway.registry import get_llm_provider_registry
from llm_gateway.service import get_llm_gateway_service


client = TestClient(create_app())


def _clear_gateway_caches() -> None:
    get_llm_gateway_service.cache_clear()
    get_llm_provider_registry.cache_clear()


def test_models_test_reports_not_configured_without_key(monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    _clear_gateway_caches()
    response = client.post("/api/models/test", json={"model_alias": "light-screening-default"})
    payload = response.json()

    assert response.status_code == 200
    assert payload["data"]["status"] == "NOT_CONFIGURED"
    assert payload["data"]["provider"] == "deepseek"
    assert payload["data"]["schema_passed"] is False
    assert "authorization" not in json.dumps(payload).lower()
    assert "deepseek_api_key" not in json.dumps(payload).lower()
    _clear_gateway_caches()


def test_models_test_can_use_mock_alias_without_network() -> None:
    _clear_gateway_caches()
    response = client.post("/api/v1/models/test", json={"model_alias": "mock-fast"})
    payload = response.json()

    assert response.status_code == 200
    assert payload["data"]["status"] == "SUCCESS"
    assert payload["data"]["provider"] == "mock"
    assert payload["data"]["schema_passed"] is True
    assert "content" not in payload["data"]
    _clear_gateway_caches()


def test_models_test_unknown_alias_is_categorized() -> None:
    _clear_gateway_caches()
    response = client.post("/api/models/test", json={"model_alias": "missing-alias"})
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "PROVIDER_ERROR"
    _clear_gateway_caches()
