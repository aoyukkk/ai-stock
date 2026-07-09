from fastapi.testclient import TestClient

from backend.main import create_app
import backend.api.real_quant as real_quant_api


def test_manual_real_quant_api_supports_baostock_only(monkeypatch, tmp_path) -> None:
    captured = {}

    def fake_run_real_quant_top500(**kwargs):
        captured.update(kwargs)
        return {
            "report_path": str(tmp_path / "api_baostock_only.json"),
            "quant_mode": "baostock_historical_degraded",
            "requested_trade_date": None,
            "actual_trade_date": "2026-07-08",
            "baostock_date_attempts": [{"date": "2026-07-08", "row_count": 20}],
            "akshare_proxy_mode": "env",
            "akshare_proxy_env_detected": False,
            "universe_count": 5203,
            "filtered_count": 5000,
            "scored_count": 20,
            "top_count": 20,
            "skipped_count": 0,
            "failed_count": 0,
            "warnings": ["emotion_score fallback"],
            "top_stocks": [{"rank": 1, "stock_code": "000001", "stock_name": "Ping An Bank"}],
            "no_llm_call_verified": True,
        }

    monkeypatch.setattr(real_quant_api, "run_real_quant_top500", fake_run_real_quant_top500)
    client = TestClient(create_app())

    response = client.post(
        "/api/pools/run-quant-real",
        json={
            "provider": "baostock",
            "history_provider": "baostock",
            "top_n": 500,
            "sample_limit": 50,
            "max_lookback_days": 20,
            "save_to_db": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"success", "data", "error", "trace_id"}
    assert payload["success"] is True
    assert payload["error"] is None
    assert payload["data"]["quant_mode"] == "baostock_historical_degraded"
    assert payload["data"]["top_count"] == 20
    assert captured["provider"] == "baostock"
    assert captured["history_provider"] == "baostock"
    assert captured["max_lookback_days"] == 20
    assert captured["save_to_db"] is False
