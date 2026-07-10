from __future__ import annotations

from scripts import run_real_quant_top500 as real_quant
from tests.test_real_quant_tushare_batch_cache import _install


def test_stk_limit_enters_top_stock_fields_and_factor_detail(monkeypatch, tmp_path) -> None:
    _install(monkeypatch, tmp_path)

    report = real_quant.run_real_quant_top500(
        provider="tushare",
        history_provider="tushare",
        backup_history_provider="baostock",
        top_n=5,
        sample_limit=5,
        start_date="2026-01-01",
        end_date="2026-01-25",
        output=tmp_path / "limit.json",
        progress=False,
        use_cache=True,
    )

    assert report["factor_data_coverage"]["stk_limit"] is True
    top = report["top_stocks"][0]
    assert top["limit_status"] in {
        "NORMAL",
        "NEAR_LIMIT_UP",
        "AT_LIMIT_UP",
        "OPENED_LIMIT_UP",
        "CONSECUTIVE_LIMIT_UP",
        "NEAR_LIMIT_DOWN",
        "AT_LIMIT_DOWN",
        "OPENED_LIMIT_DOWN",
        "CONSECUTIVE_LIMIT_DOWN",
        "LIMIT_DATA_MISSING",
        "NOT_APPLICABLE",
    }
    assert "limit_up_price" in top
    assert "limit_down_price" in top
    assert "limit_risk_note" in top
    detail_names = {detail["factor_name"] for detail in top["factor_detail"]}
    assert {"limit_up_price", "limit_down_price", "limit_status", "limit_risk_note"}.issubset(detail_names)


def test_stk_limit_missing_falls_back_without_stopping(monkeypatch, tmp_path) -> None:
    _install(monkeypatch, tmp_path)
    base_provider = real_quant.TushareMarketDataProvider

    class TempNoLimitProvider(base_provider):
        def query_trade_date_endpoint(self, api_name, *args, **kwargs):
            if api_name == "stk_limit":
                from datasource.tushare_provider import TushareEndpointResult

                return TushareEndpointResult(api_name="stk_limit", status="empty", records=[])
            return super().query_trade_date_endpoint(api_name, *args, **kwargs)

    monkeypatch.setattr(real_quant, "TushareMarketDataProvider", TempNoLimitProvider)

    report = real_quant.run_real_quant_top500(
        provider="tushare",
        history_provider="tushare",
        backup_history_provider="baostock",
        top_n=5,
        sample_limit=5,
        start_date="2026-01-01",
        end_date="2026-01-25",
        output=tmp_path / "missing_limit.json",
        progress=False,
        use_cache=True,
    )

    assert report["scored_count"] == 5
    assert report["top_stocks"][0]["limit_status"] == "LIMIT_DATA_MISSING"
