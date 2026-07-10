from __future__ import annotations

import sys
import types

from scripts import prewarm_tushare_trade_date_cache as prewarm


class FakePro:
    def trade_cal(self, **kwargs):
        return [{"cal_date": "20260101", "is_open": 1}, {"cal_date": "20260102", "is_open": 1}]

    def daily(self, **kwargs):
        return [{"ts_code": "000001.SZ", "trade_date": kwargs.get("trade_date"), "open": 10, "high": 11, "low": 9, "close": 10}]

    def daily_basic(self, **kwargs):
        return [{"ts_code": "000001.SZ", "trade_date": kwargs.get("trade_date"), "pe": 10}]

    def moneyflow(self, **kwargs):
        if kwargs.get("trade_date") == "20260102":
            raise RuntimeError("moneyflow failed")
        return [{"ts_code": "000001.SZ", "trade_date": kwargs.get("trade_date"), "net_mf_amount": 1}]


def _install(monkeypatch, tmp_path, token: str = "secret-token") -> None:
    monkeypatch.setenv("TUSHARE_TOKEN", token)
    monkeypatch.setitem(sys.modules, "tushare", types.SimpleNamespace(pro_api=lambda token: FakePro()))

    class TempProvider(prewarm.TushareMarketDataProvider):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, cache_dir=tmp_path / "tushare", request_interval_seconds=0, **kwargs)

    monkeypatch.setattr(prewarm, "TushareMarketDataProvider", TempProvider)


def test_prewarm_tushare_trade_date_cache_is_importable() -> None:
    assert callable(prewarm.run_prewarm)


def test_prewarm_missing_token_returns_clear_report(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)

    report = prewarm.run_prewarm(
        start_date="2026-01-01",
        end_date="2026-01-02",
        output=tmp_path / "missing.json",
        progress=False,
    )

    assert report["success_count"] == 0
    assert report["errors_sample"][0]["code"] == "MissingToken"


def test_prewarm_writes_trade_date_cache_without_token_leak(monkeypatch, tmp_path) -> None:
    _install(monkeypatch, tmp_path)

    report = prewarm.run_prewarm(
        start_date="2026-01-01",
        end_date="2026-01-02",
        interfaces="daily,daily_basic",
        output=tmp_path / "prewarm.json",
        progress=False,
    )

    raw = (tmp_path / "prewarm.json").read_text(encoding="utf-8")
    assert report["trade_date_count"] == 2
    assert report["success_count"] == 4
    assert (tmp_path / "tushare" / "trade_date" / "daily" / "20260101.json").exists()
    assert "secret-token" not in raw


def test_prewarm_single_trade_date_interface_failure_does_not_stop(monkeypatch, tmp_path) -> None:
    _install(monkeypatch, tmp_path)

    report = prewarm.run_prewarm(
        start_date="2026-01-01",
        end_date="2026-01-02",
        interfaces="daily,moneyflow",
        output=tmp_path / "partial.json",
        progress=False,
    )

    statuses = {(item["api_name"], item["trade_date"]): item["status"] for item in report["interface_results"]}
    assert statuses[("daily", "20260101")] == "available"
    assert statuses[("moneyflow", "20260102")] == "error"
    assert report["success_count"] >= 2
    assert report["no_llm_call_verified"] is True
