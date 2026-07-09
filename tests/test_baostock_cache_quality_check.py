import json

from datasource.baostock_provider import BaoStockMarketDataProvider
from datasource.models.market import MarketStockInfo
from scripts import check_baostock_kline_cache as quality


def _stock(code):
    return MarketStockInfo(code=code, name=f"Name {code}", market="SH", status="NORMAL", source="test")


def _bar(stock_code, day):
    return {
        "stock_code": stock_code,
        "datetime": day,
        "open": 10,
        "high": 11,
        "low": 9,
        "close": 10.5,
        "pre_close": 10,
        "volume": 1000,
        "amount": 10500,
        "turnover_rate": 1.2,
        "change_percent": 5,
        "source": "test",
    }


def _patch_provider(monkeypatch, tmp_path, stocks):
    class TempBaoStock(quality.BaoStockMarketDataProvider):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, cache_dir=tmp_path / "baostock", **kwargs)

        def get_stock_list(self, *args, **kwargs):
            return stocks

    monkeypatch.setattr(quality, "BaoStockMarketDataProvider", TempBaoStock)
    return TempBaoStock()


def test_quality_check_detects_missing_cache(monkeypatch, tmp_path):
    _patch_provider(monkeypatch, tmp_path, [_stock("600000")])

    report = quality.run_quality_check(sample_limit=1, min_bars=2, output=tmp_path / "quality.json")

    assert report["checked_count"] == 1
    assert report["missing_count"] == 1
    assert report["valid_cache_count"] == 0


def test_quality_check_detects_insufficient_bars(monkeypatch, tmp_path):
    provider = _patch_provider(monkeypatch, tmp_path, [_stock("600000")])
    provider.write_kline_cache("600000", "2026-01-01", "2026-01-02", "daily", "3", [])
    path = provider._kline_cache_path("600000", "2026-01-01", "2026-01-02", "daily", "3")
    path.write_text(json.dumps([_bar("600000", "2026-01-01")]), encoding="utf-8")

    report = quality.run_quality_check(sample_limit=1, min_bars=2, output=tmp_path / "quality.json")

    assert report["missing_count"] == 0
    assert report["insufficient_count"] == 1


def test_quality_check_detects_duplicate_dates(monkeypatch, tmp_path):
    provider = _patch_provider(monkeypatch, tmp_path, [_stock("600000")])
    provider.kline_cache_dir.mkdir(parents=True, exist_ok=True)
    path = provider._kline_cache_path("600000", "2026-01-01", "2026-01-03", "daily", "3")
    path.write_text(
        json.dumps(
            [
                _bar("600000", "2026-01-01"),
                _bar("600000", "2026-01-01"),
                _bar("600000", "2026-01-02"),
            ]
        ),
        encoding="utf-8",
    )

    report = quality.run_quality_check(sample_limit=1, min_bars=2, output=tmp_path / "quality.json")

    assert report["duplicate_date_count"] == 1
    assert report["valid_cache_count"] == 0
