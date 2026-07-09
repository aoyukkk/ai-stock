import sys
import types
from datetime import date, timedelta

from datasource.baostock_provider import BaoStockMarketDataProvider
from scripts import prewarm_baostock_kline_cache as prewarm


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


def _rows(code, start_date, end_date):
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    rows = []
    day = start
    while day <= end:
        rows.append([day.isoformat(), code, "10", "11", "9", "10.5", "10", "1000", "10500", "1.2", "5"])
        day += timedelta(days=1)
    return rows


def test_incremental_update_merges_and_deduplicates_old_cache(monkeypatch, tmp_path):
    fake = types.SimpleNamespace(
        login=lambda: FakeLoginResult(),
        logout=lambda: None,
        query_all_stock=lambda day=None: FakeQueryResult([["sh.600000", "1", "Name"]]),
        query_history_k_data_plus=lambda code, *args, **kwargs: FakeQueryResult(
            _rows(code, kwargs["start_date"], kwargs["end_date"])
        ),
    )
    monkeypatch.setitem(sys.modules, "baostock", fake)

    class TempBaoStock(prewarm.BaoStockMarketDataProvider):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, cache_dir=tmp_path / "baostock", **kwargs)

    monkeypatch.setattr(prewarm, "BaoStockMarketDataProvider", TempBaoStock)

    prewarm.run_prewarm(
        sample_limit=1,
        start_date="2026-01-01",
        end_date="2026-01-03",
        output=tmp_path / "first.json",
        progress=False,
    )
    report = prewarm.run_prewarm(
        sample_limit=1,
        end_date="2026-01-05",
        incremental_days=2,
        output=tmp_path / "incremental.json",
        progress=False,
    )

    provider = BaoStockMarketDataProvider(cache_dir=tmp_path / "baostock")
    bars = provider.read_all_cached_kline("600000")
    dates = [bar.datetime for bar in bars]

    assert report["success_count"] == 1
    assert dates == [
        "2026-01-01",
        "2026-01-02",
        "2026-01-03",
        "2026-01-04",
        "2026-01-05",
    ]
    assert len(dates) == len(set(dates))
