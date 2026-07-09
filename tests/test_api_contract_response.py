from fastapi.testclient import TestClient

from backend.main import create_app


client = TestClient(create_app())


def test_api_alias_success_uses_v03_response_envelope() -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.headers["X-Trace-Id"]

    payload = response.json()
    assert set(payload) == {"success", "data", "error", "trace_id"}
    assert payload["success"] is True
    assert payload["data"]["status"] == "ok"
    assert payload["error"] is None
    assert payload["trace_id"] == response.headers["X-Trace-Id"]
    assert "code" not in payload
    assert "message" not in payload


def test_api_alias_error_uses_v03_response_envelope() -> None:
    response = client.get("/api/not-found")

    assert response.status_code == 404
    assert response.headers["X-Trace-Id"]

    payload = response.json()
    assert set(payload) == {"success", "data", "error", "trace_id"}
    assert payload["success"] is False
    assert payload["data"] is None
    assert payload["error"]["code"] == "HTTP_404"
    assert payload["error"]["message"]
    assert isinstance(payload["error"]["details"], dict)
    assert payload["trace_id"] == response.headers["X-Trace-Id"]


def test_api_v1_keeps_legacy_response_envelope() -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["code"] == "OK"
    assert payload["message"] == "ok"
    assert payload["data"]["status"] == "ok"
    assert payload["trace_id"] == response.headers["X-Trace-Id"]
