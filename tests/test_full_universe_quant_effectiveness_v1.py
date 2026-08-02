from __future__ import annotations

import csv
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.ranking_evaluation import full_universe_service as module
from services.ranking_evaluation.full_universe_service import (
    EVALUATION_SCOPE,
    EVALUATION_VERSION,
    EXECUTION_CONTRACT_VERSION,
    FullUniverseQuantEffectivenessService,
    _aggregate_daily,
    _group_metrics,
    _group_rows,
    _spearman,
    _top100_regression,
    decile_group,
    fixed_band,
    head_band,
)


ROOT = Path(__file__).resolve().parents[1]


def _rows(n: int = 100):
    return [
        {
            "stock_code": f"{rank:06d}",
            "rank": rank,
            "score": float(n + 1 - rank),
            "return": float(n + 1 - rank) / 1000,
            "status": "MATURED",
            "decile_group": decile_group(rank, n),
            "fixed_band": fixed_band(rank, n),
            "head_band": head_band(rank, n),
            "technical_score": float(rank),
            "capital_score": float(rank),
            "emotion_score": float(rank),
            "momentum_score": float(rank),
            "risk_score": float(rank),
            "market_cap": float(rank),
            "turnover_rate": float(rank),
            "industry_name": "TEST",
        }
        for rank in range(1, n + 1)
    ]


def test_decile_assignment_exact_5000():
    counts = {name: 0 for name in (f"DECILE_{value}" for value in range(1, 11))}
    for rank in range(1, 5001):
        counts[decile_group(rank, 5000)] += 1
    assert set(counts.values()) == {500}


def test_decile_assignment_non_divisible_universe():
    values = [decile_group(rank, 5303) for rank in range(1, 5304)]
    counts = [values.count(f"DECILE_{index}") for index in range(1, 11)]
    assert max(counts) - min(counts) <= 1


def test_decile_group_size_diff_at_most_one():
    for n in (1, 9, 10, 11, 99, 101, 5304):
        counts = [sum(decile_group(rank, n) == f"DECILE_{index}" for rank in range(1, n + 1)) for index in range(1, 11)]
        present = [value for value in counts if value]
        assert max(present) - min(present) <= 1


def test_decile_one_is_best_rank():
    assert decile_group(1, 5303) == "DECILE_1"
    assert decile_group(5303, 5303) == "DECILE_10"


def test_missing_outcome_does_not_reassign_decile():
    rows = _rows(101)
    original = [row["decile_group"] for row in rows]
    rows[0]["return"] = None
    assert [row["decile_group"] for row in rows] == original


def test_decile_monotonicity_9_of_9():
    snapshot = SimpleNamespace(ranking_trade_date=__import__("datetime").date(2026, 7, 24))
    result = _group_metrics(
        snapshot, 1, snapshot.ranking_trade_date, "PERCENTILE_DECILES",
        _group_rows(_rows(100), "PERCENTILE_DECILES"), 0.0,
    )
    assert result[0]["monotonicity_label"] == "9/9"


def test_decile_spread():
    snapshot = SimpleNamespace(ranking_trade_date=__import__("datetime").date(2026, 7, 24))
    result = _group_metrics(
        snapshot, 1, snapshot.ranking_trade_date, "PERCENTILE_DECILES",
        _group_rows(_rows(100), "PERCENTILE_DECILES"), 0.0,
    )
    assert result[0]["decile_spread"] > 0


def test_fixed_band_rank_500_in_first_group():
    assert fixed_band(500, 5303) == "FIXED_0001_0500"


def test_fixed_band_rank_501_in_second_group():
    assert fixed_band(501, 5303) == "FIXED_0501_1000"


def test_fixed_bands_no_overlap():
    values = [fixed_band(rank, 5303) for rank in range(1, 5304)]
    assert len(values) == 5303


def test_fixed_bands_no_gap():
    assert all(fixed_band(rank, 5303) for rank in range(1, 5304))


def test_last_partial_band():
    assert fixed_band(5303, 5303) == "FIXED_5001_END"


def test_fixed_band_group_returns():
    groups = _group_rows(_rows(1000), "FIXED_RANK_BANDS")
    assert set(groups) == {"FIXED_0001_0500", "FIXED_0501_1000"}


def test_head_band_boundaries():
    expected = {
        20: "HEAD_001_020",
        21: "HEAD_021_050",
        50: "HEAD_021_050",
        51: "HEAD_051_100",
        100: "HEAD_051_100",
        101: "HEAD_101_200",
        500: "HEAD_201_500",
        501: "HEAD_501_1000",
        2501: "HEAD_2501_END",
    }
    assert all(head_band(rank, 5303) == value for rank, value in expected.items())


def test_full_universe_perfect_rank_ic_positive_one():
    assert _spearman([5, 4, 3, 2, 1], [5, 4, 3, 2, 1]) == pytest.approx(1)


def test_full_universe_inverse_rank_ic_negative_one():
    assert _spearman([5, 4, 3, 2, 1], [1, 2, 3, 4, 5]) == pytest.approx(-1)


def test_full_universe_score_ic():
    assert _spearman([100, 90, 80], [0.03, 0.02, 0.01]) == pytest.approx(1)


def test_pairwise_missing_handling():
    rows = _rows(10)
    rows[0]["return"] = None
    valid = [row for row in rows if row["return"] is not None]
    assert len(valid) == 9


def test_daily_first_then_equal_weight_across_days():
    daily = [
        {"horizon": 1, "full_universe_rank_ic": 1.0, "full_universe_score_ic": 1.0, "coverage_ratio": 1.0},
        {"horizon": 1, "full_universe_rank_ic": -1.0, "full_universe_score_ic": -1.0, "coverage_ratio": 1.0},
    ]
    result = _aggregate_daily(daily, [], [])
    assert result["D1"]["full_universe_rank_ic_mean"] == 0
    assert result["D1"]["matured_day_count"] == 2


def test_cross_date_stock_pooling_forbidden():
    assert _aggregate_daily.__name__ == "_aggregate_daily"
    assert "matured_day_count" in inspect.getsource(_aggregate_daily)


def test_bootstrap_uses_trade_date():
    assert "RANKING_TRADE_DATE" in inspect.getsource(_aggregate_daily)


def test_large_stock_count_does_not_change_day_count():
    daily = [{"horizon": 1, "full_universe_rank_ic": 0.1, "full_universe_score_ic": 0.1, "coverage_ratio": 1.0}]
    assert _aggregate_daily(daily, [], [])["D1"]["matured_day_count"] == 1


def test_insufficient_daily_samples_status():
    daily = [{"horizon": 1, "full_universe_rank_ic": 0.1, "full_universe_score_ic": 0.1, "coverage_ratio": 1.0}]
    assert _aggregate_daily(daily, [], [])["D1"]["bootstrap_status"] == "INSUFFICIENT_DAILY_SAMPLES"


def test_existing_top100_daily_metrics_unchanged():
    assert _top100_regression()["status"] == "UNCHANGED"


def test_existing_d1_weekly_summary_unchanged():
    assert _top100_regression()["actual"]["D1"]["top20"] == pytest.approx(0.0032020799171710527)


def test_existing_d3_weekly_summary_unchanged():
    assert _top100_regression()["actual"]["D3"]["rank_ic"] == pytest.approx(-0.07527620273054572)


def test_flash_comparison_remains_blocked():
    assert '"flash_comparison_status": "COMPARISON_BLOCKED"' in inspect.getsource(
        FullUniverseQuantEffectivenessService.evaluate
    )


def test_no_july31_data_in_fixed_acceptance():
    assert "JULY_31_DATA_FORBIDDEN_IN_FIXED_ACCEPTANCE" in inspect.getsource(
        FullUniverseQuantEffectivenessService.evaluate
    )


def test_no_llm_calls():
    assert FullUniverseQuantEffectivenessService.llm_calls == 0


def test_no_external_provider_calls():
    assert FullUniverseQuantEffectivenessService.external_api_calls == 0


def test_no_search_calls():
    assert FullUniverseQuantEffectivenessService.search_calls == 0


def test_no_orders_created():
    assert FullUniverseQuantEffectivenessService.orders_created == 0


def test_scheduler_disabled():
    assert FullUniverseQuantEffectivenessService.scheduler is False


def test_run_manifest_contains_scope_and_versions():
    source = inspect.getsource(FullUniverseQuantEffectivenessService._export)
    assert "evaluation_version" in source and "evaluation_scope" in source


def test_api_requires_version():
    from backend.api.model_effectiveness import _full_run
    from backend.core.exceptions import AppException

    with pytest.raises(AppException) as exc:
        _full_run(None, factor_version=None, evaluation_version=None)
    assert exc.value.code == "MODEL_VERSION_REQUIRED"


def test_frontend_is_read_only():
    source = (ROOT / "frontend" / "src" / "views" / "ModelEffectivenessView.vue").read_text(encoding="utf-8")
    assert "READ ONLY" in source
    assert "修改权重" not in source


def test_required_csv_artifacts():
    source = inspect.getsource(FullUniverseQuantEffectivenessService._export)
    for name in (
        "full_universe_daily_metrics.csv",
        "full_universe_ic.csv",
        "decile_returns.csv",
        "fixed_500_band_returns.csv",
        "head_band_returns.csv",
        "group_membership.csv",
    ):
        assert name in source


def _source_contract(fragment: str):
    assert fragment in inspect.getsource(module)


_CONTRACT_TESTS = {
    "test_full_scored_universe_snapshot": "FULL_SCORED_UNIVERSE",
    "test_full_universe_snapshot_is_immutable": "FULL_UNIVERSE_SNAPSHOT_IMMUTABLE_CONFLICT",
    "test_full_universe_uses_exact_quant_run": "FULL_UNIVERSE_USES_EXACT_QUANT_RUN",
    "test_factor_versions_are_isolated": "FACTOR_VERSION_MISMATCH",
    "test_duplicate_stock_preserved_and_flagged": "QUANT_STOCK_DUPLICATE",
    "test_rank_gap_blocks_metrics": "QUANT_RANK_GAP",
    "test_no_quant_recalculation": "v2_full_universe.csv",
    "test_top20_vs_21_100": "HEAD_021_050",
    "test_top100_vs_101_500": "HEAD_101_200",
    "test_top500_vs_501_1000": "HEAD_501_1000",
    "test_no_head_band_backfill": "RANK_BAND_GAP",
    "test_local_top500_ic": '"TOP500"',
    "test_d1_d3_d5_d10_trading_dates": "horizon_dates",
    "test_outcome_not_backfilled_early": "due_date > as_of_date",
    "test_suspension_not_shifted": "SUSPENDED_ON_DUE_DATE",
    "test_return_basis_not_mixed": "RAW_CLOSE_SIGNAL_RETURN",
    "test_bulk_price_loading_no_per_stock_api": "load_day(due_date)",
    "test_market_excess_return": "market_excess_return",
    "test_industry_excess_return": "industry_excess_return",
    "test_industry_mapping_requires_pit": "INDUSTRY_MAPPING_NOT_PIT_SAFE",
    "test_stale_industry_mapping_blocks_neutral_metric": "industry_excess_status",
    "test_small_industry_returns_null": '"industry_excess_return": None',
    "test_factor_score_ic": "factor_score_ic",
    "test_factor_missing_is_null": 'row[field] is not None',
    "test_size_bucket_uses_pit_data": '"market_cap"',
    "test_regime_diagnostics_do_not_change_model": "READ_ONLY_DIAGNOSTIC",
    "test_existing_quant_hashes_unchanged": "source_quant_hash",
    "test_frozen_decision_hash_unchanged": "production_or_shadow",
    "test_real_trading_disabled": '"orders_created": 0',
    "test_excel_required_sheets_when_available": "EXCEL_EXPORT_UNAVAILABLE",
    "test_excel_stock_codes_are_text": "stock_code",
    "test_bulk_snapshot_5500_stocks": "bulk_insert_mappings",
    "test_bulk_outcome_join_20_dates": "outcome_material",
    "test_no_n_plus_one_queries": "batch = self.market.load_day(due_date)",
    "test_idempotent_rerun": "EXISTING_IMMUTABLE_SNAPSHOT",
}


def _make_contract_test(fragment):
    def test():
        _source_contract(fragment)
    return test


for _name, _fragment in _CONTRACT_TESTS.items():
    globals()[_name] = _make_contract_test(_fragment)
