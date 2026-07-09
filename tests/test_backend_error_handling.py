from fastapi.testclient import TestClient

from backend.main import create_app


client = TestClient(create_app())


def test_404_uses_unified_error_response() -> None:
    response = client.get("/api/v1/not-found")

    assert response.status_code == 404
    assert response.headers["X-Trace-Id"]

    payload = response.json()
    assert payload["success"] is False
    assert payload["code"] == "HTTP_404"
    assert payload["message"]
    assert payload["data"] is None
    assert payload["trace_id"] == response.headers["X-Trace-Id"]
    assert "Traceback" not in response.text
