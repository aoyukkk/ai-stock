from __future__ import annotations

import json
import sys
import types

from scripts import prewarm_tushare_cache as prewarm


class FakePro:
    def stock_basic(self, **kwargs):
        return [
            {"ts_code": "000001.SZ", "symbol": "000001", "name": "Name 1", "list_status": "L"},
            {"ts_code": "000002.SZ", "symbol": "000002", "name": "Name 2", "list_status": "L"},
        ]

    def trade_cal(self, **kwargs):
        return [{"cal_date": "20260105", "is_open": 1}]

    def daily(self, **kwargs):
        if kwargs.get("ts_code") == "000002.SZ":
            raise RuntimeError("daily failed")
        return [{"ts_code": kwargs.get("ts_code"), "trade_date": "20260105", "open": 10, "high": 11, "low": 9, "close": 10.5}]

    def daily_basic(self, **kwargs):
        return [{"ts_code": kwargs.get("ts_code"), "trade_date": "20260105", "pe": 10}]

    def adj_factor(self, **kwargs):
        return [{"ts_code": kwargs.get("ts_code"), "trade_date": "20260105", "adj_factor": 1}]

    def stk_limit(self, **kwargs):
        return [{"ts_code": kwargs.get("ts_code"), "trade_date": "20260105", "up_limit": 11, "down_limit": 9}]

    def moneyflow(self, **kwargs):
        return [{"ts_code": kwargs.get("ts_code"), "trade_date": "20260105", "net_mf_amount": 1}]


def _install_fake(monkeypatch, tmp_path, token="secret-token"):
    monkeypatch.setenv("TUSHARE_TOKEN", token)
    monkeypatch.setitem(sys.modules, "tushare", types.SimpleNamespace(pro_api=lambda token: FakePro()))

    class TempProvider(prewarm.TushareMarketDataProvider):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, cache_dir=tmp_path / "tushare", request_interval_seconds=0, **kwargs)

    monkeypatch.setattr(prewarm, "TushareMarketDataProvider", TempProvider)


def test_prewarm_tushare_cache_is_importable() -> None:
    assert callable(prewarm.run_prewarm)


def test_prewarm_missing_token_returns_clear_report(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    output = tmp_path / "missing.json"

    report = prewarm.run_prewarm(include="stock_basic", output=output, progress=False)

    assert report["token_configured"] is False
    assert report["success_count"] == 0
    assert report["errors_sample"]


def test_prewarm_writes_cache_without_token_leak(monkeypatch, tmp_path) -> None:
    _install_fake(monkeypatch, tmp_path)
    output = tmp_path / "prewarm.json"

    report = prewarm.run_prewarm(
        sample_limit=1,
        start_date="2026-01-01",
        end_date="2026-01-05",
        include="stock_basic,daily,daily_basic",
        output=output,
        progress=False,
    )

    raw = output.read_text(encoding="utf-8")
    assert report["target_stock_count"] == 1
    assert report["success_count"] >= 2
    assert "secret-token" not in raw
    assert any(path.name.endswith(".json") for path in (tmp_path / "tushare").glob("*.json"))


def test_prewarm_single_interface_failure_does_not_stop(monkeypatch, tmp_path) -> None:
    _install_fake(monkeypatch, tmp_path)

    report = prewarm.run_prewarm(
        sample_limit=2,
        start_date="2026-01-01",
        end_date="2026-01-05",
        include="stock_basic,daily,moneyflow",
        output=tmp_path / "partial.json",
        progress=False,
    )

    daily = next(item for item in report["interface_results"] if item["api_name"] == "daily")
    moneyflow = next(item for item in report["interface_results"] if item["api_name"] == "moneyflow")
    assert daily["status"] == "error"
    assert moneyflow["status"] == "available"
    assert json.loads((tmp_path / "partial.json").read_text(encoding="utf-8"))["no_llm_call_verified"] is True
