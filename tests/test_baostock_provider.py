import sys
import types

from datasource.baostock_provider import BaoStockMarketDataProvider
from datasource.models.market import KLineBar


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


def test_baostock_code_conversion() -> None:
    provider = BaoStockMarketDataProvider()

    assert provider.to_baostock_code("000001") == "sz.000001"
    assert provider.to_baostock_code("600000") == "sh.600000"
    assert provider.to_baostock_code("688001") == "sh.688001"
    assert provider.to_baostock_code("300001") == "sz.300001"


def test_baostock_provider_returns_kline_with_mock_module(monkeypatch) -> None:
    fake = types.SimpleNamespace(
        login=lambda: FakeLoginResult(),
        logout=lambda: None,
        query_history_k_data_plus=lambda *args, **kwargs: FakeQueryResult([
            ["2025-01-01", "sz.000001", "10", "11", "9", "10.5", "10", "1000", "10500", "1.2", "5"],
        ]),
    )
    monkeypatch.setitem(sys.modules, "baostock", fake)

    bars = BaoStockMarketDataProvider().get_kline("000001", "2025-01-01", "2025-01-01")

    assert isinstance(bars[0], KLineBar)
    assert bars[0].stock_code == "000001"
