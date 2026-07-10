from __future__ import annotations

import json

from scripts import run_real_quant_top500 as real_quant
from tests.test_real_quant_tushare_batch_cache import _install


def test_quant_report_factor_coverage_and_json_fields(monkeypatch, tmp_path) -> None:
    _install(monkeypatch, tmp_path)
    output = tmp_path / "coverage.json"

    report = real_quant.run_real_quant_top500(
        provider="tushare",
        history_provider="tushare",
        backup_history_provider="baostock",
        top_n=5,
        sample_limit=5,
        start_date="2026-01-01",
        end_date="2026-01-25",
        output=output,
        progress=False,
        use_cache=True,
    )

    expected_keys = {
        "daily",
        "daily_basic",
        "moneyflow",
        "stk_limit",
        "adj_factor",
        "concept",
        "top_list",
        "margin",
        "pledge",
        "unlock",
        "chip",
        "tushare_factor",
    }
    assert expected_keys.issubset(report["factor_data_coverage"])
    top = report["top_stocks"][0]
    assert {"adj_factor_available", "limit_status", "limit_up_price", "limit_down_price", "limit_risk_note"}.issubset(top)
    assert isinstance(top["factor_detail"], list)
    loaded = json.loads(output.read_text(encoding="utf-8"))
    assert loaded["top_stocks"][0]["limit_status"] == top["limit_status"]
    assert loaded["no_llm_call_verified"] is True
