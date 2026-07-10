from __future__ import annotations

from datasource.tushare_provider import TushareEndpointResult
from scripts import run_real_quant_top500 as real_quant
from tests.test_real_quant_tushare_batch_cache import _install


def test_adj_factor_enters_coverage_and_factor_detail(monkeypatch, tmp_path) -> None:
    _install(monkeypatch, tmp_path)

    report = real_quant.run_real_quant_top500(
        provider="tushare",
        history_provider="tushare",
        backup_history_provider="baostock",
        top_n=5,
        sample_limit=5,
        start_date="2026-01-01",
        end_date="2026-01-25",
        output=tmp_path / "adj.json",
        progress=False,
        use_cache=True,
    )

    assert report["factor_data_coverage"]["adj_factor"] is True
    assert report["no_llm_call_verified"] is True
    top = report["top_stocks"][0]
    assert top["adj_factor_available"] is True
    assert any(detail["factor_name"] == "adj_factor_available" for detail in top["factor_detail"])


def test_missing_adj_factor_does_not_fail_scoring(monkeypatch, tmp_path) -> None:
    _install(monkeypatch, tmp_path)

    # Keep the existing fake SDK/cache behavior, but force adj_factor to be absent.
    base_provider = real_quant.TushareMarketDataProvider

    class TempNoAdjProvider(base_provider):
        def query_trade_date_endpoint(self, api_name, *args, **kwargs):
            if api_name == "adj_factor":
                return TushareEndpointResult(api_name="adj_factor", status="empty", records=[])
            return super().query_trade_date_endpoint(api_name, *args, **kwargs)

    monkeypatch.setattr(real_quant, "TushareMarketDataProvider", TempNoAdjProvider)

    report = real_quant.run_real_quant_top500(
        provider="tushare",
        history_provider="tushare",
        backup_history_provider="baostock",
        top_n=5,
        sample_limit=5,
        start_date="2026-01-01",
        end_date="2026-01-25",
        output=tmp_path / "missing_adj.json",
        progress=False,
        use_cache=True,
    )

    assert report["scored_count"] == 5
    assert report["top_stocks"][0]["adj_factor_available"] is False
