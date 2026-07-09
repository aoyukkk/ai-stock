import json

from fastapi.testclient import TestClient

from backend.main import create_app


client = TestClient(create_app())


def test_database_health_returns_safe_unified_response() -> None:
    response = client.get("/api/v1/database/health")

    assert response.status_code == 200
    assert response.headers["X-Trace-Id"]

    payload = response.json()
    assert payload["success"] is True
    assert payload["code"] == "OK"
    assert payload["trace_id"] == response.headers["X-Trace-Id"]

    data = payload["data"]
    assert data["status"] in {"ok", "unavailable"}
    assert data["database_configured"] is True
    assert data["database_type"] in {"sqlite", "postgresql", "unknown"}
    assert isinstance(data["connected"], bool)
    assert data["real_trading_enabled"] is False

    payload_text = json.dumps(payload, ensure_ascii=False).lower()
    assert "database_url" not in payload_text
    assert "postgresql://" not in payload_text
    assert "sqlite:///" not in payload_text
    for forbidden in ("password", "token", "secret", "api_key"):
        assert forbidden not in payload_text
