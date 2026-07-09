from __future__ import annotations

from datetime import date, timedelta

from datasource.models.market import KLineBar, MarketStockInfo
from scripts import compare_tushare_baostock_kline as compare


class FakeTushareProvider:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def get_stock_list(self):
        return [
            MarketStockInfo(code="000001", name="Name 1", market="SZ", status="NORMAL"),
            MarketStockInfo(code="000002", name="Name 2", market="SZ", status="NORMAL"),
        ]

    def get_kline(self, stock_code, start_date=None, end_date=None, frequency="daily"):
        bars = _bars(stock_code, close_offset=0)
        if stock_code == "000002":
            return bars[:-1]
        return bars


class FakeBaoStockProvider:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def batch_session(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def get_kline(self, stock_code, start_date=None, end_date=None, frequency="daily"):
        if stock_code == "000001":
            return _bars(stock_code, close_offset=1.0)
        return _bars(stock_code, close_offset=0)


def test_compare_tushare_baostock_kline_is_importable() -> None:
    assert callable(compare.run_compare)


def test_compare_detects_price_mismatch_and_missing_dates(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(compare, "TushareMarketDataProvider", FakeTushareProvider)
    monkeypatch.setattr(compare, "BaoStockMarketDataProvider", FakeBaoStockProvider)

    report = compare.run_compare(
        sample_limit=2,
        start_date="2026-01-01",
        end_date="2026-01-03",
        tolerance=0.01,
        output=tmp_path / "compare.json",
        progress=False,
    )

    assert report["sample_count"] == 2
    assert report["mismatch_count"] == 2
    assert report["price_mismatch_count"] > 0
    assert report["missing_in_tushare"] == 1
    assert report["report_path"].endswith("compare.json")


def _bars(stock_code: str, close_offset: float = 0.0) -> list[KLineBar]:
    start = date(2026, 1, 1)
    return [
        KLineBar(
            stock_code=stock_code,
            datetime=(start + timedelta(days=index)).isoformat(),
            open=10 + index,
            high=11 + index,
            low=9 + index,
            close=10.5 + index + close_offset,
            pre_close=10 + index,
            volume=1000 + index,
            amount=10000 + index,
            turnover_rate=1.0,
            change_percent=1.0,
            source="fake",
        )
        for index in range(3)
    ]
