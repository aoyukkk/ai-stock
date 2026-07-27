import json

from scripts.run_real_quant_top500 import run_real_quant_top500


def test_quant_report_contains_performance_fields(tmp_path) -> None:
    output = tmp_path / "performance_report.json"

    report = run_real_quant_top500(
        provider="mock",
        history_provider="mock",
        backup_history_provider=None,
        top_n=5,
        sample_limit=5,
        output=output,
        progress=False,
    )
    persisted = json.loads(output.read_text(encoding="utf-8"))

    expected_keys = {
        "universe_fetch_seconds",
        "filter_seconds",
        "kline_fetch_seconds",
        "factor_compute_seconds",
        "ranking_seconds",
        "report_write_seconds",
        "total_seconds",
        "avg_kline_fetch_ms",
        "stocks_per_second",
        "data_fetch_workers",
        "factor_compute_workers",
        "cache_hit_count",
        "cache_miss_count",
    }
    assert expected_keys <= set(report["performance"])
    assert expected_keys <= set(persisted["performance"])
    assert report["performance"]["total_seconds"] >= 0
    assert persisted["no_llm_call_verified"] is True
