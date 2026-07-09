from fastapi.testclient import TestClient

from backend.main import create_app


client = TestClient(create_app())


ALIAS_PAIRS = [
    ("GET", "/api/health", "/api/v1/health"),
    ("GET", "/api/system/config-summary", "/api/v1/system/config-summary"),
    ("GET", "/api/database/health", "/api/v1/database/health"),
    ("GET", "/api/data-sources/status", "/api/v1/data-sources/status"),
    ("GET", "/api/quant/config", "/api/v1/quant/config"),
    ("GET", "/api/llm/status", "/api/v1/llm/status"),
    ("GET", "/api/order-price/config", "/api/v1/order-price/config"),
    ("GET", "/api/virtual-trading/account", "/api/v1/virtual-trading/account"),
    ("GET", "/api/alerts/config", "/api/v1/alerts/config"),
    ("GET", "/api/recheck/config", "/api/v1/recheck/config"),
    ("GET", "/api/review/config", "/api/v1/review/config"),
    ("GET", "/api/memory/config", "/api/v1/memory/config"),
    ("GET", "/api/config/effective", "/api/v1/config/effective"),
]


def test_api_alias_routes_reuse_existing_handlers() -> None:
    for method, new_path, legacy_path in ALIAS_PAIRS:
        new_response = client.request(method, new_path)
        legacy_response = client.request(method, legacy_path)

        assert new_response.status_code == legacy_response.status_code, new_path
        assert new_response.json()["success"] is True, new_path
        assert new_response.json()["error"] is None, new_path
        assert _stable_data(new_response.json()["data"]) == _stable_data(
            legacy_response.json()["data"]
        ), new_path


def test_legacy_api_v1_routes_remain_available() -> None:
    response = client.get("/api/v1/system/config-summary")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["code"] == "OK"
    assert payload["data"]["system"]["real_trading_enabled"] is False


def _stable_data(value):
    if isinstance(value, dict):
        return {
            key: _stable_data(item)
            for key, item in value.items()
            if key not in {"created_at", "updated_at", "trace_id"}
        }
    if isinstance(value, list):
        return [_stable_data(item) for item in value]
    return value
