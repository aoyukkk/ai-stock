from __future__ import annotations

from fastapi.testclient import TestClient

from backend.main import app


client = TestClient(app)


def test_fundamental_run_is_fail_closed_and_executes_no_queries(monkeypatch):
    monkeypatch.setenv("RUN_REAL_FUNDAMENTAL_RESEARCH", "true")
    monkeypatch.setenv("DEEPSEEK_WEB_SEARCH_ENABLED", "true")
    response = client.post(
        "/api/fundamental-research/run",
        json={"stock_codes": ["000001.SZ"], "dry_run": False, "use_real_provider": True},
    )
    body = response.json()["data"]
    assert body["status"] == "CAPABILITY_NOT_AVAILABLE_FOR_APPLICATION_API"
    assert body["real_execution_allowed"] is False
    assert body["query_count"] == 0
    assert body["source_count"] == 0


def test_position_sizing_api_is_advisory_only():
    response = client.post(
        "/api/position-sizing/evaluate",
        json={
            "account": {"equity": "100000", "available_cash": "80000"},
            "candidates": [{
                "stock_code": "000001.SZ",
                "final_score": "80",
                "entry_price": "10",
                "stop_price": "9",
                "average_daily_amount": "10000000",
            }],
        },
    )
    body = response.json()["data"]
    assert body["advisory_only"] is True
    assert body["execution_capability"] == "NONE_ADVISORY_ONLY"
    assert body["suggestions"][0]["suggested_quantity"] % 100 == 0
