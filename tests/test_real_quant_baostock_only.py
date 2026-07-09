import json
import sys
import types
from datetime import date, timedelta

import pytest

from scripts import run_real_quant_top500 as real_quant


class FakeLoginResult:
    error_code = "0"
    error_msg = ""


class FakeQueryResult:
    def __init__(self, rows: list[list[str]], error_code: str = "0", error_msg: str = "success") -> None:
        self.rows = rows
        self.index = -1
        self.error_code = error_code
        self.error_msg = error_msg

    def next(self) -> bool:
        self.index += 1
        return self.index < len(self.rows)

    def get_row_data(self) -> list[str]:
        return self.rows[self.index]


def _stock_rows(count: int = 20) -> list[list[str]]:
    rows = []
    for index in range(count):
        if index % 3 == 0:
            code = f"sh.60{index:04d}"
        elif index % 3 == 1:
            code = f"sz.00{index:04d}"
        else:
            code = f"sz.30{index:04d}"
        rows.append([code, "1", f"Name {index:04d}"])
    return rows


def _kline_rows(stock_code: str, count: int = 25) -> list[list[str]]:
    start = date(2026, 6, 1)
    rows = []
    for index in range(count):
        base = 10 + index * 0.1
        rows.append(
            [
                (start + timedelta(days=index)).isoformat(),
                stock_code,
                f"{base:.2f}",
                f"{base + 0.5:.2f}",
                f"{base - 0.4:.2f}",
                f"{base + 0.2:.2f}",
                f"{base:.2f}",
                str(100000 + index * 1000),
                str(10000000 + index * 100000),
                f"{1.0 + index * 0.01:.2f}",
                "1.0",
            ]
        )
    return rows


def _install_fake_baostock(monkeypatch, tmp_path, empty_first_day: bool = False) -> dict:
    calls = {"query_all_stock": [], "query_history": 0}

    def query_all_stock(day):
        calls["query_all_stock"].append(day)
        if empty_first_day and day == "2026-07-09":
            return FakeQueryResult([])
        return FakeQueryResult(_stock_rows(20))

    def query_history_k_data_plus(code, *args, **kwargs):
        calls["query_history"] += 1
        return FakeQueryResult(_kline_rows(code))

    fake = types.SimpleNamespace(
        login=lambda: FakeLoginResult(),
        logout=lambda: None,
        query_all_stock=query_all_stock,
        query_history_k_data_plus=query_history_k_data_plus,
    )
    monkeypatch.setitem(sys.modules, "baostock", fake)

    class TempCacheBaoStock(real_quant.BaoStockMarketDataProvider):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(cache_enabled=False, cache_dir=tmp_path / "baostock_cache")

    monkeypatch.setattr(real_quant, "BaoStockMarketDataProvider", TempCacheBaoStock)
    return calls


def test_baostock_only_mode_does_not_call_akshare(monkeypatch, tmp_path) -> None:
    calls = _install_fake_baostock(monkeypatch, tmp_path)

    class BoomAKShare:
        def __init__(self, *args, **kwargs) -> None:
            raise AssertionError("AKShareProvider must not be used in baostock-only mode")

    monkeypatch.setattr(real_quant, "AKShareMarketDataProvider", BoomAKShare)

    report = real_quant.run_real_quant_top500(
        provider="baostock",
        history_provider="baostock",
        top_n=500,
        sample_limit=20,
        trade_date="2026-07-08",
        max_lookback_days=1,
        output=tmp_path / "baostock_only.json",
        progress=False,
    )

    assert report["provider"] == "baostock"
    assert report["history_provider"] == "baostock"
    assert report["quant_mode"] == "baostock_historical_degraded"
    assert report["actual_trade_date"] == "2026-07-08"
    assert report["scored_count"] > 0
    assert report["top_count"] == min(500, report["scored_count"])
    assert report["no_llm_call_verified"] is True
    assert calls["query_history"] == report["scored_count"]


def test_baostock_only_rolls_back_empty_universe_day(monkeypatch, tmp_path) -> None:
    _install_fake_baostock(monkeypatch, tmp_path, empty_first_day=True)

    report = real_quant.run_real_quant_top500(
        provider="baostock",
        history_provider="baostock",
        top_n=5,
        sample_limit=5,
        trade_date="2026-07-09",
        max_lookback_days=2,
        output=tmp_path / "rollback.json",
        progress=False,
    )

    assert report["baostock_date_attempts"][0]["date"] == "2026-07-09"
    assert report["baostock_date_attempts"][0]["row_count"] == 0
    assert report["actual_trade_date"] == "2026-07-08"


def test_baostock_only_report_marks_fallbacks(monkeypatch, tmp_path) -> None:
    _install_fake_baostock(monkeypatch, tmp_path)
    output = tmp_path / "fallbacks.json"

    report = real_quant.run_real_quant_top500(
        provider="baostock",
        history_provider="baostock",
        top_n=5,
        sample_limit=5,
        trade_date="2026-07-08",
        output=output,
        progress=False,
    )
    persisted = json.loads(output.read_text(encoding="utf-8"))

    assert any("capital_score uses BaoStock historical" in warning for warning in report["warnings"])
    assert any("emotion_score fallback" in warning for warning in report["warnings"])
    assert persisted["quant_mode"] == "baostock_historical_degraded"


def test_baostock_only_skips_single_empty_kline_without_stopping(monkeypatch, tmp_path) -> None:
    calls = {"query_history": 0}

    def query_history_k_data_plus(code, *args, **kwargs):
        calls["query_history"] += 1
        if calls["query_history"] == 1:
            return FakeQueryResult([])
        return FakeQueryResult(_kline_rows(code))

    fake = types.SimpleNamespace(
        login=lambda: FakeLoginResult(),
        logout=lambda: None,
        query_all_stock=lambda day: FakeQueryResult(_stock_rows(5)),
        query_history_k_data_plus=query_history_k_data_plus,
    )
    monkeypatch.setitem(sys.modules, "baostock", fake)

    class TempCacheBaoStock(real_quant.BaoStockMarketDataProvider):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(cache_enabled=False, cache_dir=tmp_path / "baostock_cache")

    monkeypatch.setattr(real_quant, "BaoStockMarketDataProvider", TempCacheBaoStock)

    report = real_quant.run_real_quant_top500(
        provider="baostock",
        history_provider="baostock",
        top_n=5,
        sample_limit=5,
        trade_date="2026-07-08",
        output=tmp_path / "skip_empty.json",
        progress=False,
    )

    assert report["skipped_count"] == 1
    assert report["scored_count"] > 0
    assert report["errors_sample"][0]["code"] == "INSUFFICIENT_KLINE"
