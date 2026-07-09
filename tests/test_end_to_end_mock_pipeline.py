from fastapi.testclient import TestClient

from backend.main import create_app


client = TestClient(create_app())


def assert_ok(response):
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["code"] == "OK"
    assert payload["trace_id"]
    assert payload["data"].get("real_trading_enabled") is False
    return payload["data"]


def test_end_to_end_mock_only_pipeline() -> None:
    quant = assert_ok(client.get("/api/v1/quant/scan", params={"top_q": 8}))
    assert quant["results"]

    screening = assert_ok(
        client.get(
            "/api/v1/screening/light/run",
            params={"quant_top_q": 8, "top_n": 5},
        )
    )
    assert screening["results"]

    committee = assert_ok(
        client.get(
            "/api/v1/committee/run",
            params={"input_top_n": 5, "final_top_n": 3},
        )
    )
    assert committee["results"]

    order_price = assert_ok(
        client.get("/api/v1/order-price/plans", params={"input_top_n": 2})
    )
    assert order_price["plans"]

    account = assert_ok(client.post("/api/v1/virtual-trading/accounts/default"))
    assert account["name"] == "AI Simulation"

    virtual = assert_ok(
        client.post("/api/v1/virtual-trading/run-plans", params={"input_top_n": 2})
    )
    assert virtual["summary"]["virtual_only"] is True

    alerts = assert_ok(client.post("/api/v1/alerts/intraday/scan"))
    assert "alerts" in alerts

    review = assert_ok(
        client.post(
            "/api/v1/review/daily/run",
            json={"date": "2026-01-05", "use_mock_llm": False},
        )
    )
    assert "final_review_score" in review

    memory = assert_ok(
        client.post("/api/v1/memory/search", json={"stock_code": "000001", "top_k": 5})
    )
    assert "notes" in memory
