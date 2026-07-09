import sys
import types

from datasource.baostock_provider import BaoStockMarketDataProvider


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


def test_baostock_contract_uses_mocked_library_and_logs_out(monkeypatch, tmp_path) -> None:
    calls = {"logout": 0}
    fake = types.SimpleNamespace(
        login=lambda: FakeLoginResult(),
        logout=lambda: calls.__setitem__("logout", calls["logout"] + 1),
        query_all_stock=lambda: FakeQueryResult([["sz.000001", "Ping An Bank"]]),
        query_history_k_data_plus=lambda *args, **kwargs: FakeQueryResult([
            ["2025-01-01", "sz.000001", "10", "11", "9", "10.5", "10", "1000", "10500", "1.2", "5"],
        ]),
    )
    monkeypatch.setitem(sys.modules, "baostock", fake)

    provider = BaoStockMarketDataProvider(cache_dir=tmp_path)

    assert provider.get_stock_list()[0].code == "000001"
    assert provider.get_kline("000001", "2025-01-01", "2025-01-01")[0].source == "baostock"
    assert calls["logout"] == 2
