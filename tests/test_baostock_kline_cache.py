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


def _fake_kline_rows() -> list[list[str]]:
    return [
        ["2026-01-01", "sz.000001", "10", "11", "9", "10.5", "10", "1000", "10500", "1.2", "5"],
    ]


def test_baostock_kline_cache_miss_calls_baostock_and_writes_cache(monkeypatch, tmp_path) -> None:
    calls = {"query_history": 0}
    fake = types.SimpleNamespace(
        login=lambda: FakeLoginResult(),
        logout=lambda: None,
        query_history_k_data_plus=lambda *args, **kwargs: calls.__setitem__(
            "query_history",
            calls["query_history"] + 1,
        )
        or FakeQueryResult(_fake_kline_rows()),
    )
    monkeypatch.setitem(sys.modules, "baostock", fake)
    provider = BaoStockMarketDataProvider(cache_dir=tmp_path)

    bars = provider.get_kline("000001", "2026-01-01", "2026-01-01")

    assert bars[0].stock_code == "000001"
    assert calls["query_history"] == 1
    assert provider.cache_hit_count == 0
    assert provider.cache_miss_count == 1
    assert list((tmp_path / "kline").glob("*.json"))


def test_baostock_kline_cache_hit_avoids_baostock_request(monkeypatch, tmp_path) -> None:
    fake = types.SimpleNamespace(
        login=lambda: FakeLoginResult(),
        logout=lambda: None,
        query_history_k_data_plus=lambda *args, **kwargs: FakeQueryResult(_fake_kline_rows()),
    )
    monkeypatch.setitem(sys.modules, "baostock", fake)
    provider = BaoStockMarketDataProvider(cache_dir=tmp_path)
    provider.get_kline("000001", "2026-01-01", "2026-01-01")

    calls = {"login": 0, "query_history": 0}
    no_request_fake = types.SimpleNamespace(
        login=lambda: calls.__setitem__("login", calls["login"] + 1) or FakeLoginResult(),
        logout=lambda: None,
        query_history_k_data_plus=lambda *args, **kwargs: calls.__setitem__(
            "query_history",
            calls["query_history"] + 1,
        )
        or FakeQueryResult([]),
    )
    monkeypatch.setitem(sys.modules, "baostock", no_request_fake)
    cached_provider = BaoStockMarketDataProvider(cache_dir=tmp_path)

    bars = cached_provider.get_kline("000001", "2026-01-01", "2026-01-01")

    assert bars[0].stock_code == "000001"
    assert calls == {"login": 0, "query_history": 0}
    assert cached_provider.cache_hit_count == 1
    assert cached_provider.cache_miss_count == 0


def test_baostock_kline_refresh_cache_bypasses_existing_cache(monkeypatch, tmp_path) -> None:
    fake = types.SimpleNamespace(
        login=lambda: FakeLoginResult(),
        logout=lambda: None,
        query_history_k_data_plus=lambda *args, **kwargs: FakeQueryResult(_fake_kline_rows()),
    )
    monkeypatch.setitem(sys.modules, "baostock", fake)
    BaoStockMarketDataProvider(cache_dir=tmp_path).get_kline("000001", "2026-01-01", "2026-01-01")

    calls = {"query_history": 0}
    refresh_fake = types.SimpleNamespace(
        login=lambda: FakeLoginResult(),
        logout=lambda: None,
        query_history_k_data_plus=lambda *args, **kwargs: calls.__setitem__(
            "query_history",
            calls["query_history"] + 1,
        )
        or FakeQueryResult(_fake_kline_rows()),
    )
    monkeypatch.setitem(sys.modules, "baostock", refresh_fake)
    provider = BaoStockMarketDataProvider(cache_dir=tmp_path, refresh_kline_cache=True)

    provider.get_kline("000001", "2026-01-01", "2026-01-01")

    assert calls["query_history"] == 1
    assert provider.cache_hit_count == 0
    assert provider.cache_miss_count == 1
