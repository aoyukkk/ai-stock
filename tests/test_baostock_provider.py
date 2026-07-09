import sys
import types

from datasource.baostock_provider import BaoStockMarketDataProvider
from datasource.models.market import KLineBar, MarketStockInfo


class FakeLoginResult:
    error_code = "0"
    error_msg = ""


class FakeQueryResult:
    def __init__(self, rows: list[list[str]]) -> None:
        self.rows = rows
        self.index = -1

    def next(self) -> bool:
        self.index += 1
        return self.index < len(self.rows)

    def get_row_data(self) -> list[str]:
        return self.rows[self.index]


def test_baostock_provider_import_and_instantiate() -> None:
    assert BaoStockMarketDataProvider()


def test_baostock_provider_import_does_not_login(monkeypatch) -> None:
    calls = {"login": 0}
    fake = types.SimpleNamespace(login=lambda: calls.__setitem__("login", calls["login"] + 1))
    monkeypatch.setitem(sys.modules, "baostock", fake)

    BaoStockMarketDataProvider()

    assert calls["login"] == 0


def test_baostock_code_conversion() -> None:
    provider = BaoStockMarketDataProvider()

    assert provider.to_baostock_code("000001") == "sz.000001"
    assert provider.to_baostock_code("600000") == "sh.600000"
    assert provider.to_baostock_code("688001") == "sh.688001"
    assert provider.to_baostock_code("300001") == "sz.300001"
    assert provider.to_baostock_code("800001") == "bj.800001"
    assert provider.to_baostock_code("400001") == "bj.400001"


def test_baostock_provider_returns_stock_list_with_mock_module(monkeypatch, tmp_path) -> None:
    calls = {"logout": 0}
    fake = types.SimpleNamespace(
        login=lambda: FakeLoginResult(),
        logout=lambda: calls.__setitem__("logout", calls["logout"] + 1),
        query_all_stock=lambda: FakeQueryResult([["sz.000001", "Ping An Bank"]]),
    )
    monkeypatch.setitem(sys.modules, "baostock", fake)

    stocks = BaoStockMarketDataProvider(cache_dir=tmp_path).get_stock_list()

    assert isinstance(stocks[0], MarketStockInfo)
    assert stocks[0].code == "000001"
    assert stocks[0].source == "baostock"
    assert calls["logout"] == 1


def test_baostock_provider_returns_kline_with_mock_module(monkeypatch, tmp_path) -> None:
    calls = {"logout": 0}
    fake = types.SimpleNamespace(
        login=lambda: FakeLoginResult(),
        logout=lambda: calls.__setitem__("logout", calls["logout"] + 1),
        query_history_k_data_plus=lambda *args, **kwargs: FakeQueryResult([
            ["2025-01-01", "sz.000001", "10", "11", "9", "10.5", "10", "1000", "10500", "1.2", "5"],
        ]),
    )
    monkeypatch.setitem(sys.modules, "baostock", fake)

    bars = BaoStockMarketDataProvider(cache_dir=tmp_path).get_kline("000001", "2025-01-01", "2025-01-01")

    assert isinstance(bars[0], KLineBar)
    assert bars[0].stock_code == "000001"
    assert bars[0].source == "baostock"
    assert calls["logout"] == 1
