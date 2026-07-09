from __future__ import annotations

from fastapi.testclient import TestClient

from backend.main import create_app
import backend.api.real_quant as real_quant_api


def test_run_quant_real_api_supports_tushare_cache_contract(monkeypatch, tmp_path) -> None:
    captured = {}

    def fake_run_real_quant_top500(**kwargs):
        captured.update(kwargs)
        return {
            "report_path": str(tmp_path / "api_tushare_cache.json"),
            "provider": "tushare",
            "history_provider": "tushare",
            "backup_history_provider": "baostock",
            "quant_mode": "tushare_primary",
            "fallback_used": True,
            "fallback_reason": "000001:tushare_error:RuntimeError",
            "baostock_backup_used_count": 1,
            "requested_trade_date": None,
            "actual_trade_date": None,
            "baostock_date_attempts": [],
            "akshare_proxy_mode": "env",
            "akshare_proxy_env_detected": False,
            "universe_count": 5529,
            "filtered_count": 5318,
            "scored_count": 300,
            "top_count": 500,
            "factor_data_coverage": {"daily": True, "daily_basic": True, "moneyflow": True},
            "tushare_api_success_count": 10,
            "tushare_api_empty_count": 1,
            "tushare_api_error_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "warnings": [],
            "performance": {"cache_hit_count": 5, "cache_miss_count": 1},
            "top_stocks": [],
            "no_llm_call_verified": True,
        }

    monkeypatch.setattr(real_quant_api, "run_real_quant_top500", fake_run_real_quant_top500)
    client = TestClient(create_app())

    response = client.post(
        "/api/pools/run-quant-real",
        json={
            "provider": "tushare",
            "history_provider": "tushare",
            "backup_history_provider": "baostock",
            "top_n": 500,
            "sample_limit": 300,
            "use_cache": True,
            "save_to_db": False,
            "output": str(tmp_path / "api_tushare_cache.json"),
        },
    )

    payload = response.json()
    assert response.status_code == 200
    assert set(payload) == {"success", "data", "error", "trace_id"}
    assert payload["success"] is True
    assert payload["data"]["provider"] == "tushare"
    assert payload["data"]["history_provider"] == "tushare"
    assert payload["data"]["backup_history_provider"] == "baostock"
    assert payload["data"]["baostock_backup_used_count"] == 1
    assert payload["data"]["tushare_api_success_count"] == 10
    assert payload["data"]["factor_data_coverage"]["daily"] is True
    assert captured["sample_limit"] == 300
    assert captured["use_cache"] is True
    assert captured["save_to_db"] is False
