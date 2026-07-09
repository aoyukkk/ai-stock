import json
import sys
import types
from datetime import date, timedelta

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


def _stock_rows(count=3):
    return [[f"sh.60{index:04d}", "1", f"Name {index}"] for index in range(count)]


def _kline_rows(code, start_date, end_date, fail_code=None):
    if fail_code and code.endswith(fail_code):
        raise RuntimeError("kline failed")
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    rows = []
    day = start
    while day <= end:
        rows.append(
            [
                day.isoformat(),
                code,
                "10",
                "11",
                "9",
                "10.5",
                "10",
                "1000",
                "10500",
                "1.2",
                "5",
            ]
        )
        day += timedelta(days=1)
    return rows


def _install_fake(monkeypatch, tmp_path, stock_count=3, fail_code=None):
    calls = {"query_history": 0}

    def query_history_k_data_plus(code, *args, **kwargs):
        calls["query_history"] += 1
        return FakeQueryResult(_kline_rows(code, kwargs["start_date"], kwargs["end_date"], fail_code=fail_code))

    fake = types.SimpleNamespace(
        login=lambda: FakeLoginResult(),
        logout=lambda: None,
        query_all_stock=lambda day=None: FakeQueryResult(_stock_rows(stock_count)),
        query_history_k_data_plus=query_history_k_data_plus,
    )
    monkeypatch.setitem(sys.modules, "baostock", fake)

    class TempBaoStock(prewarm.BaoStockMarketDataProvider):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, cache_dir=tmp_path / "baostock", **kwargs)

    monkeypatch.setattr(prewarm, "BaoStockMarketDataProvider", TempBaoStock)
    return calls


def test_prewarm_script_is_importable():
    assert callable(prewarm.run_prewarm)


def test_prewarm_supports_sample_limit_and_report(monkeypatch, tmp_path):
    calls = _install_fake(monkeypatch, tmp_path, stock_count=3)
    output = tmp_path / "prewarm.json"

    report = prewarm.run_prewarm(
        sample_limit=2,
        start_date="2026-01-01",
        end_date="2026-01-03",
        output=output,
        progress=False,
    )

    assert output.is_file()
    assert report["target_count"] == 2
    assert report["success_count"] == 2
    assert report["failed_count"] == 0
    assert calls["query_history"] == 2
    assert json.loads(output.read_text(encoding="utf-8"))["no_llm_call_verified"] is True


def test_prewarm_refresh_cache_forces_request(monkeypatch, tmp_path):
    calls = _install_fake(monkeypatch, tmp_path, stock_count=1)

    prewarm.run_prewarm(
        sample_limit=1,
        start_date="2026-01-01",
        end_date="2026-01-03",
        output=tmp_path / "first.json",
        progress=False,
    )
    report = prewarm.run_prewarm(
        sample_limit=1,
        start_date="2026-01-01",
        end_date="2026-01-03",
        refresh_cache=True,
        output=tmp_path / "refresh.json",
        progress=False,
    )

    assert calls["query_history"] == 2
    assert report["refreshed_count"] == 1


def test_prewarm_single_stock_failure_does_not_stop(monkeypatch, tmp_path):
    _install_fake(monkeypatch, tmp_path, stock_count=3, fail_code="0001")

    report = prewarm.run_prewarm(
        sample_limit=3,
        start_date="2026-01-01",
        end_date="2026-01-03",
        output=tmp_path / "failure.json",
        progress=False,
    )

    assert report["success_count"] == 2
    assert report["failed_count"] == 1
    assert report["errors_sample"][0]["stock_code"] == "600001"
