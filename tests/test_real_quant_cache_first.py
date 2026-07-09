import sys
import types
from datetime import date, timedelta

from datasource.baostock_provider import BaoStockMarketDataProvider
from datasource.models.market import KLineBar
from scripts import run_real_quant_top500 as real_quant


class FakeLoginResult:
    error_code = "0"
    error_msg = ""


class FakeQueryResult:
    def __init__(self, rows):
        self.rows = rows
        self.index = -1
        self.error_code = "0"
        self.error_msg = "success"

    def next(self):
        self.index += 1
        return self.index < len(self.rows)

    def get_row_data(self):
        return self.rows[self.index]


def _stock_rows():
    return [["sh.600000", "1", "Name"]]


def _kline_rows(code, count=25):
    start = date(2026, 1, 1)
    rows = []
    for index in range(count):
        day = start + timedelta(days=index)
        rows.append([day.isoformat(), code, "10", "11", "9", "10.5", "10", "1000", "10500", "1.2", "5"])
    return rows


def _bars(count=25):
    return [
        KLineBar(
            stock_code="600000",
            datetime=(date(2026, 1, 1) + timedelta(days=index)).isoformat(),
            open=10,
            high=11,
            low=9,
            close=10.5,
            pre_close=10,
            volume=1000,
            amount=10500,
            turnover_rate=1.2,
            change_percent=5,
            source="test",
        )
        for index in range(count)
    ]


def _patch_provider(monkeypatch, tmp_path):
    class TempBaoStock(real_quant.BaoStockMarketDataProvider):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, cache_dir=tmp_path / "baostock", **kwargs)

    monkeypatch.setattr(real_quant, "BaoStockMarketDataProvider", TempBaoStock)
    return TempBaoStock()


def test_real_quant_cache_hit_does_not_call_baostock_kline(monkeypatch, tmp_path):
    provider = _patch_provider(monkeypatch, tmp_path)
    provider.write_kline_cache("600000", "2026-01-01", "2026-01-25", "daily", "3", _bars(25))
    calls = {"query_history": 0}
    fake = types.SimpleNamespace(
        login=lambda: FakeLoginResult(),
        logout=lambda: None,
        query_all_stock=lambda day=None: FakeQueryResult(_stock_rows()),
        query_history_k_data_plus=lambda *args, **kwargs: calls.__setitem__(
            "query_history",
            calls["query_history"] + 1,
        )
        or FakeQueryResult([]),
    )
    monkeypatch.setitem(sys.modules, "baostock", fake)

    report = real_quant.run_real_quant_top500(
        provider="baostock",
        history_provider="baostock",
        top_n=5,
        sample_limit=1,
        start_date="2026-01-01",
        end_date="2026-01-25",
        output=tmp_path / "cache_hit.json",
        progress=False,
    )

    assert calls["query_history"] == 0
    assert report["performance"]["cache_hit_count"] == 1
    assert report["performance"]["cache_miss_count"] == 0
    assert report["no_llm_call_verified"] is True


def test_real_quant_cache_insufficient_calls_baostock_to_fill(monkeypatch, tmp_path):
    provider = _patch_provider(monkeypatch, tmp_path)
    provider.write_kline_cache("600000", "2026-01-01", "2026-01-05", "daily", "3", _bars(5))
    calls = {"query_history": 0}
    fake = types.SimpleNamespace(
        login=lambda: FakeLoginResult(),
        logout=lambda: None,
        query_all_stock=lambda day=None: FakeQueryResult(_stock_rows()),
        query_history_k_data_plus=lambda code, *args, **kwargs: calls.__setitem__(
            "query_history",
            calls["query_history"] + 1,
        )
        or FakeQueryResult(_kline_rows(code, count=25)),
    )
    monkeypatch.setitem(sys.modules, "baostock", fake)

    report = real_quant.run_real_quant_top500(
        provider="baostock",
        history_provider="baostock",
        top_n=5,
        sample_limit=1,
        start_date="2026-01-01",
        end_date="2026-01-25",
        output=tmp_path / "cache_fill.json",
        progress=False,
    )

    assert calls["query_history"] == 1
    assert report["performance"]["cache_insufficient_count"] == 1
    assert report["performance"]["cache_miss_count"] == 1
    assert report["scored_count"] == 1
