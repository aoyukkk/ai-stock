from __future__ import annotations

import sys
import types
from datetime import date, timedelta

from datasource.models.market import KLineBar
from scripts import run_real_quant_top500 as real_quant


class RealQuantFakeTusharePro:
    def stock_basic(self, **kwargs):
        rows = []
        for index in range(8):
            code = f"00000{index + 1}.SZ"
            rows.append(
                {
                    "ts_code": code,
                    "symbol": code.split(".")[0],
                    "name": f"Name {index + 1}",
                    "industry": "Test",
                    "market": "主板",
                    "exchange": "SZSE",
                    "list_status": "L",
                    "list_date": "20200101",
                }
            )
        return rows

    def daily(self, **kwargs):
        ts_code = kwargs.get("ts_code", "000001.SZ")
        if ts_code == "000001.SZ" and kwargs.get("start_date"):
            raise RuntimeError("network timeout")
        count = 1 if not kwargs.get("start_date") else 25
        return _rows(ts_code, count=count)

    def daily_basic(self, **kwargs):
        return [{"ts_code": kwargs.get("ts_code", "000001.SZ"), "trade_date": "20260125", "turnover_rate": 2.0, "volume_ratio": 1.1, "pe": 10, "pb": 1.0}]

    def moneyflow(self, **kwargs):
        return [{"ts_code": kwargs.get("ts_code", "000001.SZ"), "trade_date": "20260125", "net_mf_amount": 5.0, "buy_lg_amount": 4.0, "sell_lg_amount": 1.0}]

    def fina_indicator(self, **kwargs):
        return [{"ts_code": kwargs.get("ts_code", "000001.SZ"), "roe": 10.0, "debt_to_assets": 35.0}]

    def concept(self, **kwargs):
        return [{"code": "C1", "name": "Test Concept"}]


def _rows(ts_code: str, count: int = 25) -> list[dict]:
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


class FakeBaoStockProvider:
    name = "baostock"
    cache_hit_count = 0
    cache_miss_count = 0
    cache_insufficient_count = 0
    cache_refresh_count = 0
    last_actual_trade_date = None
    last_date_attempts = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    def start_session(self) -> None:
        pass

    def end_session(self) -> None:
        pass

    def get_kline(self, stock_code, start_date=None, end_date=None, frequency="daily"):
        return [
            KLineBar(
                stock_code=str(stock_code).zfill(6),
                datetime=(date(2026, 1, 1) + timedelta(days=index)).isoformat(),
                open=10 + index * 0.1,
                high=10.5 + index * 0.1,
                low=9.8 + index * 0.1,
                close=10.2 + index * 0.1,
                pre_close=10 + index * 0.1,
                volume=100000 + index,
                amount=1000000 + index,
                turnover_rate=1.0,
                change_percent=1.0,
                source="baostock",
            )
            for index in range(25)
        ]


def test_real_quant_tushare_uses_baostock_backup(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TUSHARE_TOKEN", "fake-token")
    monkeypatch.setitem(sys.modules, "tushare", types.SimpleNamespace(pro_api=lambda token: RealQuantFakeTusharePro()))
    monkeypatch.setattr(real_quant, "BaoStockMarketDataProvider", FakeBaoStockProvider)

    report = real_quant.run_real_quant_top500(
        provider="tushare",
        history_provider="tushare",
        backup_history_provider="baostock",
        top_n=5,
        sample_limit=8,
        start_date="2026-01-01",
        end_date="2026-01-25",
        output=tmp_path / "tushare_top500.json",
        progress=False,
        use_cache=False,
    )

    assert report["provider"] == "tushare"
    assert report["history_provider"] == "tushare"
    assert report["backup_history_provider"] == "baostock"
    assert report["fallback_used"] is True
    assert "000001" in report["fallback_reason"]
    assert report["factor_data_coverage"]["daily"] is True
    assert report["factor_data_coverage"]["daily_basic"] is True
    assert report["factor_data_coverage"]["moneyflow"] is True
    assert report["no_llm_call_verified"] is True
    assert report["scored_count"] > 0
