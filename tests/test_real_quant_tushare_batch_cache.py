from __future__ import annotations

import sys
import types
from datetime import date, timedelta

from datasource.models.market import KLineBar
from scripts import run_real_quant_top500 as real_quant


class BatchFakePro:
    def stock_basic(self, **kwargs):
        return [
            {
                "ts_code": f"000{index:03d}.SZ",
                "symbol": f"000{index:03d}",
                "name": f"Name {index}",
                "industry": "Test",
                "list_status": "L",
                "exchange": "SZSE",
            }
            for index in range(1, 13)
        ]

    def trade_cal(self, **kwargs):
        start = date(2026, 1, 1)
        return [{"cal_date": (start + timedelta(days=index)).strftime("%Y%m%d"), "is_open": 1} for index in range(25)]

    def daily(self, **kwargs):
        assert "ts_code" not in kwargs
        trade_date = kwargs.get("trade_date")
        return [_daily_row(f"000{index:03d}.SZ", trade_date, index) for index in range(1, 13)]

    def daily_basic(self, **kwargs):
        assert "ts_code" not in kwargs
        return [
            {"ts_code": f"000{index:03d}.SZ", "trade_date": kwargs.get("trade_date"), "turnover_rate": 2, "pe": 10, "pb": 1}
            for index in range(1, 13)
        ]

    def adj_factor(self, **kwargs):
        assert "ts_code" not in kwargs
        return [
            {"ts_code": f"000{index:03d}.SZ", "trade_date": kwargs.get("trade_date"), "adj_factor": 1.0 + index / 100}
            for index in range(1, 13)
        ]

    def moneyflow(self, **kwargs):
        assert "ts_code" not in kwargs
        return [
            {"ts_code": f"000{index:03d}.SZ", "trade_date": kwargs.get("trade_date"), "net_mf_amount": 1, "buy_lg_amount": 1}
            for index in range(1, 13)
        ]

    def stk_limit(self, **kwargs):
        assert "ts_code" not in kwargs
        return [
            {"ts_code": f"000{index:03d}.SZ", "trade_date": kwargs.get("trade_date"), "up_limit": 11, "down_limit": 9}
            for index in range(1, 13)
        ]

    def ths_index(self, **kwargs):
        return [{"ts_code": "885800.TI", "name": "Concept"}]


class CountingBaoStockProvider:
    name = "baostock"
    cache_hit_count = 0
    cache_miss_count = 0
    cache_insufficient_count = 0
    cache_refresh_count = 0
    last_actual_trade_date = None
    last_date_attempts = []
    call_count = 0

    def __init__(self, *args, **kwargs) -> None:
        pass

    def start_session(self) -> None:
        pass

    def end_session(self) -> None:
        pass

    def get_kline(self, stock_code, start_date=None, end_date=None, frequency="daily"):
        self.__class__.call_count += 1
        return [
            KLineBar(
                stock_code=str(stock_code).zfill(6),
                datetime=(date(2026, 1, 1) + timedelta(days=index)).isoformat(),
                open=10,
                high=11,
                low=9,
                close=10,
                pre_close=10,
                volume=1000,
                amount=10000,
                turnover_rate=1,
                change_percent=0,
                source="baostock",
            )
            for index in range(25)
        ]


def test_real_quant_uses_trade_date_batch_cache(monkeypatch, tmp_path) -> None:
    _install(monkeypatch, tmp_path)

    report = real_quant.run_real_quant_top500(
        provider="tushare",
        history_provider="tushare",
        backup_history_provider="baostock",
        top_n=5,
        sample_limit=12,
        start_date="2026-01-01",
        end_date="2026-01-25",
        output=tmp_path / "batch.json",
        progress=False,
        use_cache=True,
    )

    assert report["trade_date_cache_used"] is True
    assert report["trade_date_cache_miss_count"] > 0
    assert report["per_stock_api_call_count"] == 0
    assert report["baostock_backup_used_count"] == 0
    assert report["factor_data_coverage"]["daily"] is True
    assert report["factor_data_coverage"]["adj_factor"] is True
    assert report["factor_data_coverage"]["daily_basic"] is True
    assert report["factor_data_coverage"]["moneyflow"] is True
    assert report["no_llm_call_verified"] is True
    assert CountingBaoStockProvider.call_count == 0


def _install(monkeypatch, tmp_path) -> None:
    CountingBaoStockProvider.call_count = 0
    monkeypatch.setenv("TUSHARE_TOKEN", "fake-token")
    monkeypatch.setitem(sys.modules, "tushare", types.SimpleNamespace(pro_api=lambda token: BatchFakePro()))
    monkeypatch.setattr(real_quant, "BaoStockMarketDataProvider", CountingBaoStockProvider)

    class TempTushareProvider(real_quant.TushareMarketDataProvider):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, cache_dir=tmp_path / "tushare", request_interval_seconds=0, **kwargs)

    monkeypatch.setattr(real_quant, "TushareMarketDataProvider", TempTushareProvider)


def _daily_row(ts_code: str, trade_date: str, index: int) -> dict:
    offset = int(str(trade_date)[-2:]) / 100
    return {
        "ts_code": ts_code,
        "trade_date": trade_date,
        "open": 10 + index + offset,
        "high": 11 + index + offset,
        "low": 9 + index + offset,
        "close": 10.5 + index + offset,
        "pre_close": 10 + index + offset,
        "pct_chg": 1.0,
        "vol": 100000 + index,
        "amount": 1000000 + index,
    }
