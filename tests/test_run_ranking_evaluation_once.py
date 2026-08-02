from __future__ import annotations

from types import SimpleNamespace

from scripts.run_ranking_evaluation_once import _canonical_factor


def test_canonical_factor_preserves_v2_identity_when_web_published() -> None:
    run = SimpleNamespace(
        factor_version="TUSHARE_QUANT_V2_CORRECTED_SHADOW",
        actionable=True,
    )

    assert _canonical_factor(run) == "TUSHARE_QUANT_V2_CORRECTED_SHADOW"


def test_canonical_factor_maps_actionable_legacy_run_to_baseline() -> None:
    run = SimpleNamespace(
        factor_version="TUSHARE_BASELINE_V1",
        actionable=True,
    )

    assert _canonical_factor(run) == "TUSHARE_BASELINE_V1"
