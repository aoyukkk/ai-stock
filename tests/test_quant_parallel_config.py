import sys

from scripts import smoke_real_quant_top500
from scripts.run_real_quant_top500 import _effective_data_fetch_workers, _effective_factor_workers


def test_baostock_data_fetch_workers_are_capped_for_session_safety() -> None:
    assert _effective_data_fetch_workers("baostock", 2) == 1
    assert _effective_data_fetch_workers("mock", 2) == 2


def test_factor_workers_currently_fall_back_to_single_process() -> None:
    assert _effective_factor_workers("auto") == 1
    assert _effective_factor_workers("2") == 1
    assert _effective_factor_workers("bad") == 1


def test_smoke_script_parses_cache_and_worker_args(monkeypatch, tmp_path) -> None:
    captured = {}

    def fake_run_real_quant_top500(**kwargs):
        captured.update(kwargs)
        return {
            "provider": kwargs["provider"],
            "history_provider": kwargs["history_provider"],
            "universe_count": 1,
            "filtered_count": 1,
            "scored_count": 1,
            "top_count": 1,
            "no_llm_call_verified": True,
            "performance": {"total_seconds": 0.0, "cache_hit_count": 0, "cache_miss_count": 0},
            "report_path": str(kwargs["output"]),
        }

    monkeypatch.setattr(smoke_real_quant_top500, "run_real_quant_top500", fake_run_real_quant_top500)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "smoke_real_quant_top500.py",
            "--provider",
            "mock",
            "--history-provider",
            "mock",
            "--use-cache",
            "false",
            "--refresh-cache",
            "--data-fetch-workers",
            "2",
            "--factor-workers",
            "auto",
            "--output",
            str(tmp_path / "report.json"),
        ],
    )

    assert smoke_real_quant_top500.main() == 0
    assert captured["use_cache"] is False
    assert captured["refresh_cache"] is True
    assert captured["data_fetch_workers"] == 2
    assert captured["factor_workers"] == "auto"
