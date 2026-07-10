from __future__ import annotations

import sys
import types

from datasource.tushare_provider import TushareMarketDataProvider


class FakePro:
    def trade_cal(self, **kwargs):
        return [
            {"cal_date": "20260101", "is_open": 1},
            {"cal_date": "20260102", "is_open": 0},
            {"cal_date": "20260105", "is_open": 1},
        ]

    def daily(self, **kwargs):
        if kwargs.get("trade_date") == "20260105":
            return []
        return [
            {"ts_code": "000001.SZ", "trade_date": kwargs.get("trade_date"), "open": 10, "high": 11, "low": 9, "close": 10.5},
            {"ts_code": "000002.SZ", "trade_date": kwargs.get("trade_date"), "open": 20, "high": 21, "low": 19, "close": 20.5},
        ]

    def daily_basic(self, **kwargs):
        return [{"ts_code": "000001.SZ", "trade_date": kwargs.get("trade_date"), "pe": 10, "pb": 1}]

    def moneyflow(self, **kwargs):
        return [{"ts_code": "000001.SZ", "trade_date": kwargs.get("trade_date"), "net_mf_amount": 1}]


def test_trade_date_cache_writes_and_reads_without_token_leak(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TUSHARE_TOKEN", "secret-token")
    monkeypatch.setitem(sys.modules, "tushare", types.SimpleNamespace(pro_api=lambda token: FakePro()))
    provider = TushareMarketDataProvider(cache_dir=tmp_path, request_interval_seconds=0)

    result = provider.query_trade_date_endpoint("daily", "2026-01-01")
    cached = provider.query_trade_date_endpoint("daily", "2026-01-01")

    assert result.status == "available"
    assert cached.status == "available"
    assert provider.trade_date_cache_miss_count == 1
    assert provider.trade_date_cache_hit_count == 1
    assert provider.trade_date_cache_path("daily", "20260101").exists()
    assert "secret-token" not in provider.trade_date_cache_path("daily", "20260101").read_text(encoding="utf-8")


def test_trade_cal_mock_returns_open_trade_dates(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TUSHARE_TOKEN", "secret-token")
    monkeypatch.setitem(sys.modules, "tushare", types.SimpleNamespace(pro_api=lambda token: FakePro()))
    provider = TushareMarketDataProvider(cache_dir=tmp_path, request_interval_seconds=0)

    dates = provider.get_open_trade_dates("2026-01-01", "2026-01-05")

    assert dates == ["20260101", "20260105"]


def test_missing_token_returns_clear_trade_date_error(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    provider = TushareMarketDataProvider(cache_dir=tmp_path, request_interval_seconds=0)

    result = provider.query_trade_date_endpoint("daily", "2026-01-01")

    assert result.status == "not_configured"
    assert result.error_type == "MissingToken"
    assert "TUSHARE_TOKEN" in (result.error_message or "")


def test_get_kline_uses_trade_date_cache_without_per_stock_daily_api(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TUSHARE_TOKEN", "secret-token")
    monkeypatch.setitem(sys.modules, "tushare", types.SimpleNamespace(pro_api=lambda token: FakePro()))
    provider = TushareMarketDataProvider(cache_dir=tmp_path, request_interval_seconds=0)

    bars = provider.get_kline("000001", "2026-01-01", "2026-01-05")

    assert [bar.stock_code for bar in bars] == ["000001"]
    assert provider.per_stock_api_call_count == 0
    assert provider.trade_date_cache_miss_count >= 1
