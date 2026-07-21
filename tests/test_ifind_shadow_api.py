from __future__ import annotations

from fastapi.testclient import TestClient

from backend.main import create_app


def test_realtime_provider_status_is_safe_and_shadow_disabled(monkeypatch):
    monkeypatch.setenv("IFIND_HTTP_ENABLED", "false")
    response = TestClient(create_app()).get("/api/workbench/realtime/provider-status")
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["integration_mode"] == "SHADOW"
    assert payload["enabled"] is False
    assert "access_token" not in str(payload).lower()


def test_realtime_refresh_does_not_call_ifind_when_disabled(monkeypatch):
    monkeypatch.setenv("IFIND_HTTP_ENABLED", "false")
    response = TestClient(create_app()).post(
        "/api/workbench/realtime/refresh?trade_date=2026-07-14",
        json={"scope": "STOCKS", "stock_codes": ["600000.SH"], "force": True},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "PROVIDER_DISABLED"
    assert data["pool_count"] == 1
