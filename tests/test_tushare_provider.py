from __future__ import annotations

import sys
import types
from datetime import date, timedelta
from decimal import Decimal

import pytest

from datasource.exceptions import DataSourceError
from datasource.models.market import KLineBar
from datasource.tushare_provider import MISSING_TOKEN_MESSAGE, TushareMarketDataProvider, _records


class FakePro:
    def __init__(self, calls: dict[str, int]) -> None:
        self.calls = calls

    def stock_basic(self, **kwargs):
        self.calls["stock_basic"] = self.calls.get("stock_basic", 0) + 1
        return [
            {
                "ts_code": "000001.SZ",
                "symbol": "000001",
                "name": "Ping An Bank",
                "industry": "Bank",
                "market": "主板",
                "exchange": "SZSE",
                "list_status": "L",
                "list_date": "19910403",
            }
        ]

    def trade_cal(self, **kwargs):
        return [{"exchange": "SSE", "cal_date": "20260105", "is_open": "1", "pretrade_date": "20260102"}]

    def daily(self, **kwargs):
        return _kline_rows(kwargs.get("ts_code", "000001.SZ"))

    def weekly(self, **kwargs):
        return _kline_rows(kwargs.get("ts_code", "000001.SZ"), count=2)

    def monthly(self, **kwargs):
        return _kline_rows(kwargs.get("ts_code", "000001.SZ"), count=1)

    def adj_factor(self, **kwargs):
        return [{"ts_code": "000001.SZ", "trade_date": "20260105", "adj_factor": 1.0}]

    def daily_basic(self, **kwargs):
        return [{"ts_code": "000001.SZ", "trade_date": "20260105", "turnover_rate": 2.5, "volume_ratio": 1.2, "pe": 8.0, "pb": 0.8}]

    def moneyflow(self, **kwargs):
        return [{"ts_code": "000001.SZ", "trade_date": "20260105", "net_mf_amount": 120.0, "buy_lg_amount": 60.0, "sell_lg_amount": 20.0}]

    def ths_index(self, **kwargs):
        return [{"ts_code": "885800.TI", "name": "AI", "type": "N"}]

    def ths_member(self, **kwargs):
        return [{"ts_code": "885800.TI", "con_code": "000001.SZ", "con_name": "Ping An Bank"}]

    def top_list(self, **kwargs):
        return [{"trade_date": "20260105", "ts_code": "000001.SZ", "net_amount": 1000.0}]

    def margin(self, **kwargs):
        return [{"trade_date": "20260105", "exchange_id": "SSE", "rzrqye": 1000.0}]

    def cyq_chips(self, **kwargs):
        raise RuntimeError("没有权限: cyq_chips")

    def stk_factor_pro(self, **kwargs):
        raise RuntimeError("没有权限: stk_factor_pro")

    def stk_limit(self, **kwargs):
        return [{"trade_date": "20260105", "ts_code": "000001.SZ", "pre_close": 10.0, "up_limit": 11.0, "down_limit": 9.0}]

    def fina_indicator(self, **kwargs):
        return [{"ts_code": "000001.SZ", "roe": 12.5, "debt_to_assets": 35.0, "profit_dedt": 100.0}]


def _kline_rows(ts_code: str, count: int = 25) -> list[dict]:
    start = date(2026, 1, 1)
    rows = []
    for index in range(count):
        rows.append(
            {
                "ts_code": ts_code,
                "trade_date": (start + timedelta(days=index)).strftime("%Y%m%d"),
                "open": 10 + index * 0.1,
                "high": 10.5 + index * 0.1,
                "low": 9.8 + index * 0.1,
                "close": 10.2 + index * 0.1,
                "pre_close": 10 + index * 0.1,
                "pct_chg": 1.0,
                "vol": 100000 + index,
                "amount": 1000000 + index,
            }
        )
    return rows


def _install_fake_tushare(monkeypatch) -> dict[str, int]:
    calls: dict[str, int] = {}

    def pro_api(token):
        assert token == "fake-token"
        return FakePro(calls)

    monkeypatch.setitem(sys.modules, "tushare", types.SimpleNamespace(pro_api=pro_api))
    monkeypatch.setenv("TUSHARE_TOKEN", "fake-token")
    return calls


def test_tushare_provider_import_does_not_import_sdk(monkeypatch) -> None:
    monkeypatch.delitem(sys.modules, "tushare", raising=False)
    provider = TushareMarketDataProvider()

    assert provider.name == "tushare"
    assert "tushare" not in sys.modules


def test_tushare_missing_token_returns_clear_error(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.delitem(sys.modules, "tushare", raising=False)
    provider = TushareMarketDataProvider(cache_dir=tmp_path, cache_enabled=False)

    result = provider.query_endpoint("stock_basic")

    assert result.status == "not_configured"
    assert result.error_message == MISSING_TOKEN_MESSAGE
    assert "tushare" not in sys.modules


def test_tushare_stock_basic_and_trade_calendar(monkeypatch, tmp_path) -> None:
    _install_fake_tushare(monkeypatch)
    provider = TushareMarketDataProvider(cache_dir=tmp_path, cache_enabled=False, request_interval_seconds=0)

    stocks = provider.get_stock_list()
    calendar = provider.get_trade_calendar("2026-01-05", "2026-01-05")

    assert stocks[0].code == "000001"
    assert stocks[0].source == "tushare"
    assert calendar.status == "available"


def test_tushare_stock_list_can_force_refresh(monkeypatch, tmp_path) -> None:
    calls = _install_fake_tushare(monkeypatch)
    provider = TushareMarketDataProvider(
        cache_dir=tmp_path,
        cache_enabled=True,
        request_interval_seconds=0,
    )

    provider.get_stock_list()
    provider.get_stock_list()
    provider.get_stock_list(use_cache=False)

    assert calls["stock_basic"] == 2


def test_tushare_market_data_endpoints(monkeypatch, tmp_path) -> None:
    _install_fake_tushare(monkeypatch)
    provider = TushareMarketDataProvider(cache_dir=tmp_path, cache_enabled=False, request_interval_seconds=0)

    assert provider.get_kline("000001", "2026-01-01", "2026-01-30")[0].source == "tushare"
    assert provider.get_adj_factor("000001").status == "available"
    assert provider.get_daily_basic("000001").status == "available"
    assert provider.get_moneyflow("000001").status == "available"
    assert provider.get_concept_list().status == "available"
    assert provider.get_concept_members().status == "available"
    assert provider.get_top_list("2026-01-05").status == "available"
    assert provider.get_margin_summary("2026-01-05").status == "available"
    assert provider.get_limit_price("000001").source == "tushare"
    assert provider.get_finance("000001").source == "tushare"
    assert provider.get_capital_flow("000001").source == "tushare"


def test_tushare_finance_normalizes_nan_values(monkeypatch, tmp_path) -> None:
    _install_fake_tushare(monkeypatch)

    class NanFinancePro(FakePro):
        def daily_basic(self, **kwargs):
            return [{"ts_code": "000001.SZ", "trade_date": "20260105", "pe": Decimal("NaN"), "pb": float("nan")}]

    def pro_api(token):
        return NanFinancePro({})

    monkeypatch.setitem(sys.modules, "tushare", types.SimpleNamespace(pro_api=pro_api))
    provider = TushareMarketDataProvider(cache_dir=tmp_path, cache_enabled=False, request_interval_seconds=0)

    finance = provider.get_finance("000001")

    assert finance.pe == 0
    assert finance.pb == 0


def test_tushare_records_normalize_mojibake_and_nan() -> None:
    rows = _records([{"name": "\u9a9e\u51b2\u7568\u95be\u60f0\ue511", "pe": float("nan")}])

    assert rows[0]["name"] == "\u5e73\u5b89\u94f6\u884c"
    assert rows[0]["pe"] is None


def test_tushare_permission_denied_returns_warning_status(monkeypatch, tmp_path) -> None:
    _install_fake_tushare(monkeypatch)
    provider = TushareMarketDataProvider(cache_dir=tmp_path, cache_enabled=False, request_interval_seconds=0)

    chip = provider.get_chip_distribution("000001")
    factor = provider.get_tushare_factors("000001")

    assert chip.status == "permission_denied"
    assert factor.status == "permission_denied"


def test_tushare_kline_falls_back_to_baostock(monkeypatch, tmp_path) -> None:
    _install_fake_tushare(monkeypatch)

    class FailingTushare(TushareMarketDataProvider):
        def _records_or_raise(self, *args, **kwargs):
            raise DataSourceError("tushare daily failed")

    class FakeBackup:
        name = "baostock"

        def get_kline(self, stock_code, start_date=None, end_date=None, frequency="daily"):
            return [
                KLineBar(
                    stock_code="000001",
                    datetime="2026-01-01",
                    open=10,
                    high=11,
                    low=9,
                    close=10.5,
                    pre_close=10,
                    volume=1000,
                    amount=10000,
                    turnover_rate=1.0,
                    change_percent=1.0,
                    source="baostock",
                )
            ]

    provider = FailingTushare(cache_dir=tmp_path, cache_enabled=False, backup_provider=FakeBackup())

    bars = provider.get_kline("000001", "2026-01-01", "2026-01-01")

    assert bars[0].source == "baostock"
    assert provider.last_fallback_used is True


def test_open_trade_dates_fall_back_to_verified_daily_cache(monkeypatch, tmp_path) -> None:
    _install_fake_tushare(monkeypatch)
    provider = TushareMarketDataProvider(cache_dir=tmp_path, request_interval_seconds=0)
    provider.get_trade_calendar = lambda **kwargs: types.SimpleNamespace(status="empty", records=[])
    daily_dir = tmp_path / "trade_date" / "daily"
    daily_dir.mkdir(parents=True)
    for name in ("20260713.json", "20260714.json", "20260716.json", "not-a-date.json"):
        (daily_dir / name).write_text("[]", encoding="utf-8")

    dates = provider.get_open_trade_dates("2026-07-14", "2026-07-16")

    assert dates == ["20260714", "20260716"]
