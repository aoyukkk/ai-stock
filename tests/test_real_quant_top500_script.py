import json

from scripts.run_real_quant_top500 import run_real_quant_top500


def test_real_quant_top500_script_runs_with_mock_provider(tmp_path) -> None:
    output = tmp_path / "mock_quant_report.json"

    report = run_real_quant_top500(
        provider="mock",
        history_provider="mock",
        backup_history_provider=None,
        top_n=500,
        sample_limit=20,
        output=output,
        progress=False,
    )

    assert output.is_file()
    persisted = json.loads(output.read_text(encoding="utf-8"))
    assert report["universe_count"] >= 1
    assert report["filtered_count"] >= report["scored_count"]
    assert report["scored_count"] > 0
    assert report["top_count"] == min(500, report["scored_count"])
    assert report["no_llm_call_verified"] is True
    assert persisted["top_stocks"][0]["stock_code"]
