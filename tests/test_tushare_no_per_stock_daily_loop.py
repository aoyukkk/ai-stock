from __future__ import annotations

from tests.test_real_quant_tushare_batch_cache import _install
from scripts import run_real_quant_top500 as real_quant


def test_tushare_quant_does_not_call_per_stock_daily_loop(monkeypatch, tmp_path) -> None:
    _install(monkeypatch, tmp_path)

    report = real_quant.run_real_quant_top500(
        provider="tushare",
        history_provider="tushare",
        backup_history_provider="baostock",
        top_n=5,
        sample_limit=5,
        start_date="2026-01-01",
        end_date="2026-01-25",
        output=tmp_path / "no_loop.json",
        progress=False,
        use_cache=True,
    )

    assert report["scored_count"] == 5
    assert report["per_stock_api_call_count"] == 0
    assert report["trade_date_cache_used"] is True
