from fastapi.testclient import TestClient

from backend.main import create_app


client = TestClient(create_app())


def assert_health_response(path: str) -> None:
    response = client.get(path)

    assert response.status_code == 200
    assert response.headers["X-Trace-Id"]

    payload = response.json()
    assert payload["success"] is True
    assert payload["code"] == "OK"
    assert payload["message"] == "ok"
    assert payload["trace_id"] == response.headers["X-Trace-Id"]
    assert payload["data"]["status"] == "ok"
    assert payload["data"]["service"] == "AI Trader Assistant"
    assert payload["data"]["real_trading_enabled"] is False
    assert payload["data"]["external_services_connected"] is False


def test_root_health() -> None:
    assert_health_response("/health")


def test_api_v1_health() -> None:
    assert_health_response("/api/v1/health")


def test_trace_id_header_is_reused() -> None:
    response = client.get("/health", headers={"X-Trace-Id": "phase-1-test"})

    assert response.status_code == 200
    assert response.headers["X-Trace-Id"] == "phase-1-test"
    assert response.json()["trace_id"] == "phase-1-test"
