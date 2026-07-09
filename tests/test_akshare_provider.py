import sys
import types

from datasource.akshare_provider import AKShareMarketDataProvider
from datasource.models.market import KLineBar, MarketStockInfo


class FakeFrame:
    def __init__(self, records: list[dict]) -> None:
        self.records = records

    def to_dict(self, orient: str = "records") -> list[dict]:
        assert orient == "records"
        return self.records


def test_akshare_provider_import_and_instantiate() -> None:
    assert AKShareMarketDataProvider()


def test_akshare_provider_returns_stock_list_with_mock_module(monkeypatch) -> None:
    fake = types.SimpleNamespace(
        stock_zh_a_spot_em=lambda: FakeFrame([
            {"代码": "000001", "名称": "Ping An Bank", "行业": "Bank"},
        ])
    )
    monkeypatch.setitem(sys.modules, "akshare", fake)

    stocks = AKShareMarketDataProvider().get_stock_list()

    assert isinstance(stocks[0], MarketStockInfo)
    assert stocks[0].code == "000001"


def test_akshare_provider_returns_kline_with_mock_module(monkeypatch) -> None:
    fake = types.SimpleNamespace(
        stock_zh_a_hist=lambda **kwargs: FakeFrame([
            {
                "日期": "2025-01-01",
                "开盘": 10,
                "最高": 11,
                "最低": 9,
                "收盘": 10.5,
                "昨收": 10,
                "成交量": 1000,
                "成交额": 10500,
                "换手率": 1.2,
                "涨跌幅": 5,
            }
        ])
    )
    monkeypatch.setitem(sys.modules, "akshare", fake)

    bars = AKShareMarketDataProvider().get_kline("000001", "2025-01-01", "2025-01-01")

    assert isinstance(bars[0], KLineBar)
    assert bars[0].close == 10.5
