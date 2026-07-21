from __future__ import annotations

from fastapi.testclient import TestClient

from backend.main import create_app


def test_post_close_api_waits_before_close_and_never_creates_orders(monkeypatch):
    monkeypatch.setattr("post_close.service._market_session", lambda *_: "OPEN")
    response = TestClient(create_app()).post("/api/workbench/post-close-actions/run-fast", json={"trade_date": "2026-07-15", "run_mode": "POST_CLOSE_FAST"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "WAITING_FOR_POST_CLOSE_RUN"
    assert data["llm_calls"] == 0
    assert data["orders_created"] == 0


def test_position_current_endpoint_does_not_expose_secrets():
    response = TestClient(create_app()).get("/api/workbench/positions/current")
    assert response.status_code == 200
    assert "token" not in response.text.lower()
    assert "password" not in response.text.lower()
    data = response.json()["data"]
    assert data["required_scopes"] == ["HUMAN_REFERENCE"]
    assert data["scope_status"]["AI_SIMULATION"] == "NOT_REQUIRED"
