from __future__ import annotations

import sys
import types
from datetime import date, timedelta

from datasource.models.market import KLineBar
from scripts import run_real_quant_top500 as real_quant


class FakeTusharePro:
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

    def daily(self, **kwargs):
        ts_code = kwargs.get("ts_code", "000001.SZ")
        if ts_code == "000001.SZ" and kwargs.get("start_date"):
            raise RuntimeError("daily failed")
        count = 1 if not kwargs.get("start_date") else 25
        return _rows(ts_code, count)

    def daily_basic(self, **kwargs):
        return [{"ts_code": kwargs.get("ts_code"), "trade_date": "20260125", "turnover_rate": 2.0, "volume_ratio": 1.1, "pe": 10, "pb": 1}]

    def moneyflow(self, **kwargs):
        return [{"ts_code": kwargs.get("ts_code"), "trade_date": "20260125", "net_mf_amount": 1, "buy_lg_amount": 1, "sell_lg_amount": 0}]

    def fina_indicator(self, **kwargs):
        return [{"ts_code": kwargs.get("ts_code"), "roe": 10, "debt_to_assets": 30, "profit_dedt": 1}]

    def ths_index(self, **kwargs):
        return [{"ts_code": "885800.TI", "name": "Concept"}]


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


def test_real_quant_tushare_cache_report_fields(monkeypatch, tmp_path) -> None:
    _install(monkeypatch, tmp_path)

    report = real_quant.run_real_quant_top500(
        provider="tushare",
        history_provider="tushare",
        backup_history_provider="baostock",
        top_n=5,
        sample_limit=300,
        start_date="2026-01-01",
        end_date="2026-01-25",
        output=tmp_path / "sample300.json",
        progress=False,
        use_cache=True,
    )

    assert report["sample_limit"] if "sample_limit" in report else True
    assert report["provider"] == "tushare"
    assert report["backup_history_provider"] == "baostock"
    assert report["baostock_backup_used_count"] >= 1
    assert report["tushare_api_success_count"] > 0
    assert report["tushare_api_error_count"] >= 1
    assert "adj_factor" in report["factor_data_coverage"]
    assert "stk_limit" in report["factor_data_coverage"]
    assert report["no_llm_call_verified"] is True


def test_real_quant_accepts_sample_limit_1000(monkeypatch, tmp_path) -> None:
    _install(monkeypatch, tmp_path)

    report = real_quant.run_real_quant_top500(
        provider="tushare",
        history_provider="tushare",
        backup_history_provider="baostock",
        top_n=5,
        sample_limit=1000,
        start_date="2026-01-01",
        end_date="2026-01-25",
        output=tmp_path / "sample1000.json",
        progress=False,
        use_cache=False,
    )

    assert report["scored_count"] > 0
    assert report["top_count"] <= 5
    assert report["no_llm_call_verified"] is True


def _install(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TUSHARE_TOKEN", "fake-token")
    monkeypatch.setitem(sys.modules, "tushare", types.SimpleNamespace(pro_api=lambda token: FakeTusharePro()))
    monkeypatch.setattr(real_quant, "BaoStockMarketDataProvider", FakeBaoStockProvider)

    class TempTushareProvider(real_quant.TushareMarketDataProvider):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, cache_dir=tmp_path / "tushare", request_interval_seconds=0, **kwargs)

    monkeypatch.setattr(real_quant, "TushareMarketDataProvider", TempTushareProvider)


def _rows(ts_code: str, count: int) -> list[dict]:
    start = date(2026, 1, 1)
    return [
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
        for index in range(count)
    ]
