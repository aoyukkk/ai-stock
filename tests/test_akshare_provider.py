import sys
import types

import pytest

from datasource.akshare_provider import AKShareMarketDataProvider
from datasource.exceptions import DataSourceError
from datasource.models.market import KLineBar, MarketStockInfo


class FakeFrame:
    def __init__(self, records: list[dict]) -> None:
        self.records = records

    def to_dict(self, orient: str = "records") -> list[dict]:
        assert orient == "records"
        return self.records


def test_akshare_provider_import_and_instantiate() -> None:
    assert AKShareMarketDataProvider()


def test_akshare_provider_import_does_not_require_network_module(monkeypatch) -> None:
    monkeypatch.delitem(sys.modules, "akshare", raising=False)

    provider = AKShareMarketDataProvider()

    assert provider.name == "akshare"


def test_akshare_provider_returns_stock_list_with_mock_module(monkeypatch, tmp_path) -> None:
    fake = types.SimpleNamespace(
        stock_zh_a_spot_em=lambda: FakeFrame([
            {
                "\u4ee3\u7801": "000001",
                "\u540d\u79f0": "Ping An Bank",
                "\u884c\u4e1a": "Bank",
                "\u6700\u65b0\u4ef7": 10.5,
                "\u6210\u4ea4\u989d": 100000000,
                "\u6210\u4ea4\u91cf": 1000000,
            },
        ])
    )
    monkeypatch.setitem(sys.modules, "akshare", fake)

    stocks = AKShareMarketDataProvider(cache_dir=tmp_path).get_stock_list()

    assert isinstance(stocks[0], MarketStockInfo)
    assert stocks[0].code == "000001"
    assert stocks[0].source == "akshare"
    assert stocks[0].raw_data


def test_akshare_provider_returns_kline_with_mock_module(monkeypatch, tmp_path) -> None:
    fake = types.SimpleNamespace(
        stock_zh_a_hist=lambda **kwargs: FakeFrame([
            {
                "\u65e5\u671f": "2025-01-01",
                "\u5f00\u76d8": 10,
                "\u6700\u9ad8": 11,
                "\u6700\u4f4e": 9,
                "\u6536\u76d8": 10.5,
                "\u6628\u6536": 10,
                "\u6210\u4ea4\u91cf": 1000,
                "\u6210\u4ea4\u989d": 10500,
                "\u6362\u624b\u7387": 1.2,
                "\u6da8\u8dcc\u5e45": 5,
            }
        ])
    )
    monkeypatch.setitem(sys.modules, "akshare", fake)

    bars = AKShareMarketDataProvider(cache_dir=tmp_path).get_kline("000001", "2025-01-01", "2025-01-01")

    assert isinstance(bars[0], KLineBar)
    assert bars[0].close == 10.5
    assert bars[0].source == "akshare"
    assert bars[0].raw_data


def test_akshare_provider_tolerates_missing_fields(monkeypatch, tmp_path) -> None:
    fake = types.SimpleNamespace(
        stock_zh_a_spot_em=lambda: FakeFrame([{"\u4ee3\u7801": "1"}]),
    )
    monkeypatch.setitem(sys.modules, "akshare", fake)

    stocks = AKShareMarketDataProvider(cache_dir=tmp_path).get_stock_list()

    assert stocks[0].code == "000001"
    assert stocks[0].name == "000001"


def test_akshare_provider_reports_clear_errors(monkeypatch, tmp_path) -> None:
    fake = types.SimpleNamespace(
        stock_zh_a_spot_em=lambda: (_ for _ in ()).throw(RuntimeError("network down")),
    )
    monkeypatch.setitem(sys.modules, "akshare", fake)

    with pytest.raises(DataSourceError, match="AKShare stock list request failed"):
        AKShareMarketDataProvider(cache_dir=tmp_path).get_stock_list()
