import sys
import types

from datasource.baostock_provider import BaoStockMarketDataProvider


class FakeLoginResult:
    error_code = "0"
    error_msg = ""


class FakeQueryResult:
    def __init__(self, rows: list[list[str]], error_code: str = "0", error_msg: str = "") -> None:
        self.rows = rows
        self.index = -1
        self.error_code = error_code
        self.error_msg = error_msg

    def next(self) -> bool:
        self.index += 1
        return self.index < len(self.rows)

    def get_row_data(self) -> list[str]:
        return self.rows[self.index]


def test_baostock_import_does_not_login(monkeypatch) -> None:
    calls = {"login": 0}
    fake = types.SimpleNamespace(login=lambda: calls.__setitem__("login", calls["login"] + 1))
    monkeypatch.setitem(sys.modules, "baostock", fake)

    BaoStockMarketDataProvider()

    assert calls["login"] == 0


def test_baostock_rolls_back_dates_when_current_day_empty(monkeypatch, tmp_path) -> None:
    requested_days = []

    def query_all_stock(day):
        requested_days.append(day)
        if day == "2026-07-07":
            return FakeQueryResult([["sz.000001", "Ping An Bank"]])
        return FakeQueryResult([])

    fake = types.SimpleNamespace(
        login=lambda: FakeLoginResult(),
        logout=lambda: None,
        query_all_stock=query_all_stock,
    )
    monkeypatch.setitem(sys.modules, "baostock", fake)

    provider = BaoStockMarketDataProvider(cache_dir=tmp_path)
    stocks = provider.get_stock_list(trade_date="2026-07-09", max_lookback_days=3)

    assert requested_days == ["2026-07-09", "2026-07-08", "2026-07-07"]
    assert provider.last_actual_trade_date == "2026-07-07"
    assert stocks[0].code == "000001"


def test_baostock_returns_clear_error_when_all_empty(monkeypatch, tmp_path) -> None:
    fake = types.SimpleNamespace(
        login=lambda: FakeLoginResult(),
        logout=lambda: None,
        query_all_stock=lambda day: FakeQueryResult([]),
    )
    monkeypatch.setitem(sys.modules, "baostock", fake)

    provider = BaoStockMarketDataProvider(cache_dir=tmp_path)
    stocks = provider.get_stock_list(trade_date="2026-07-09", max_lookback_days=2)

    assert stocks == []
    assert provider.last_actual_trade_date is None
    assert len(provider.last_date_attempts) == 3
    assert "empty after 3 date attempts" in provider.last_error_message


def test_baostock_does_not_filter_all_supported_stocks(monkeypatch, tmp_path) -> None:
    fake = types.SimpleNamespace(
        login=lambda: FakeLoginResult(),
        logout=lambda: None,
        query_all_stock=lambda day: FakeQueryResult(
            [
                ["sh.000001", "Shanghai Index"],
                ["sz.399001", "Shenzhen Index"],
                ["sh.600000", "SPDB"],
                ["sz.000001", "Ping An Bank"],
                ["sz.300001", "ChiNext Stock"],
                ["bj.430001", "BSE Stock"],
            ]
        ),
    )
    monkeypatch.setitem(sys.modules, "baostock", fake)

    stocks = BaoStockMarketDataProvider(cache_dir=tmp_path).get_stock_list(
        trade_date="2026-07-09",
        max_lookback_days=0,
    )

    assert {stock.code for stock in stocks} == {"600000", "000001", "300001", "430001"}
