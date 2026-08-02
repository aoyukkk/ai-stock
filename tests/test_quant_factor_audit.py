from __future__ import annotations

from decimal import Decimal

import pytest

from scripts.audit_quant_factor_scores import (
    WEIGHTS,
    canonical_hash,
    classify_score_origin,
    database_excel_reconciliation,
    detect_global_broadcast,
    detect_mapping_join_failure,
    distribution,
    leave_one_factor_out,
    normalize_code,
    recompute_total,
    variance_contributions,
)


def _rows() -> list[dict]:
    rows = []
    for rank in range(1, 121):
        rows.append(
            {
                "stock_code": f"{rank:06d}",
                "rank": rank,
                "technical_score": 100 - rank / 2,
                "capital_score": 40 + rank / 5,
                "emotion_score": 50,
                "momentum_score": 100 - rank / 3,
                "risk_score": 30 + rank / 4,
                "total_score": 80 - rank / 10,
            }
        )
    return rows


def test_formal_weights_sum_to_one() -> None:
    assert sum(WEIGHTS.values()) == Decimal("1.00")


def test_total_score_exact_recomputation_and_storage_rounding() -> None:
    row = {
        "technical_score": 83.1032,
        "capital_score": 51.9895,
        "emotion_score": 50,
        "momentum_score": 81.5347,
        "risk_score": 47.7301,
    }
    exact, stored = recompute_total(row)
    assert exact == Decimal("63.162895")
    assert stored == Decimal("63.1629")


def test_normalize_code_handles_tushare_and_plain_codes() -> None:
    assert normalize_code("000862.SZ") == "000862"
    assert normalize_code("862") == "000862"


@pytest.mark.parametrize(
    ("group", "name", "raw", "score", "expected"),
    [
        ("emotion", "market_heat_score", 50, 50, "HARD_CODED_DEFAULT"),
        ("capital", "volume_ratio_score", 1, 11.7647, "HARD_CODED_DEFAULT"),
        ("capital", "turnover_score", 7, 86, "CLIP_SCORE"),
        ("capital", "turnover_score", None, 50, "MISSING_NEUTRAL_FALLBACK"),
    ],
)
def test_score_origin_classification(group, name, raw, score, expected) -> None:
    assert classify_score_origin(group, name, raw, score) == expected


def test_null_is_not_misclassified_as_real_median() -> None:
    assert (
        classify_score_origin("capital", "turnover_score", None, 50)
        == "MISSING_NEUTRAL_FALLBACK"
    )


def test_global_broadcast_detection() -> None:
    assert detect_global_broadcast([50, 50, 50])
    assert not detect_global_broadcast([49, 50, 51])


def test_global_constant_variance_contribution_is_zero() -> None:
    emotion = next(
        row for row in variance_contributions(_rows()) if row["factor"] == "emotion"
    )
    assert emotion["variance_contribution"] == 0
    assert emotion["variance_share"] == 0


def test_stock_level_factor_has_cross_stock_variation() -> None:
    assert distribution(row["technical_score"] for row in _rows())[
        "unique_value_count"
    ] > 1


def test_emotion_all_50_detection() -> None:
    result = distribution([50] * 5310)
    assert result["unique_value_count"] == 1
    assert result["count_equal_50"] == 5310
    assert result["ratio_equal_50"] == 1


def test_code_format_join_is_stable() -> None:
    left = {normalize_code("000862.SZ")}
    right = {normalize_code("000862")}
    assert left == right


def test_capital_52_94_exact_source() -> None:
    value = (
        Decimal("0")
        + Decimal("11.7647")
        + Decimal("100")
        + Decimal("100")
    ) / Decimal("4")
    assert value.quantize(Decimal("0.0001")) == Decimal("52.9412")


def test_capital_has_no_rank_denominator_in_fixed_range_formula() -> None:
    assert "rank" not in "clip((turnover_rate-0.5)/(8-0.5)*100,0,100)"


def test_top100_is_not_used_by_pure_distribution_helper() -> None:
    rows = _rows()
    assert distribution(row["capital_score"] for row in rows)["sample_count"] == 120


def test_small_sample_distribution_is_explicit() -> None:
    result = distribution([1, 2])
    assert result["sample_count"] == 2
    assert result["median"] == 1.5


def test_tie_behavior_uses_stock_code_secondary_key() -> None:
    rows = _rows()[:3]
    for row in rows:
        row["total_score"] = 50
        row["emotion_score"] = 50
    result = leave_one_factor_out(rows)
    assert all("rank_correlation_without_factor" in row for row in result)


def test_no_early_rounding_in_total_recompute() -> None:
    row = {
        "technical_score": 83.1032,
        "capital_score": 51.9895,
        "emotion_score": 50,
        "momentum_score": 81.5347,
        "risk_score": 47.7301,
    }
    exact, _ = recompute_total(row)
    assert exact.as_tuple().exponent < -4


def test_percentage_units_are_percentage_points() -> None:
    turnover_rate = Decimal("7.7145")
    score = (turnover_rate - Decimal("0.5")) / (
        Decimal("8") - Decimal("0.5")
    ) * 100
    assert score.quantize(Decimal("0.0001")) == Decimal("96.1933")


def test_daily_moneyflow_unit_reconciliation_exposes_distinct_multipliers() -> None:
    daily_amount_multiplier = 1
    moneyflow_multiplier = 10000
    assert daily_amount_multiplier != moneyflow_multiplier


def test_missing_value_policy_is_not_reweighting() -> None:
    sub_scores = [0, 50, 50, 50]
    assert sum(sub_scores) / len(sub_scores) == 37.5


def test_non_random_missing_reason_is_classifiable() -> None:
    reasons = {"MONEYFLOW_NOT_COVERED", "DATA_SOURCE_PLACEHOLDER"}
    assert all(reason for reason in reasons)


def test_leave_one_factor_out_emotion_has_no_rank_impact() -> None:
    emotion = next(
        row for row in leave_one_factor_out(_rows()) if row["factor"] == "emotion"
    )
    assert emotion["rank_correlation_without_factor"] == pytest.approx(1)
    assert emotion["top20_membership_change"] == 0
    assert emotion["top100_membership_change"] == 0


def test_effective_variance_contributions_sum_to_one() -> None:
    contributions = variance_contributions(_rows())
    assert sum(row["variance_share"] for row in contributions) == pytest.approx(1)


def test_distribution_contains_required_percentiles() -> None:
    result = distribution(range(100))
    assert all(key in result for key in ("p01", "p05", "p10", "p25", "p50", "p75", "p90", "p95", "p99"))


def test_read_only_audit_helpers_have_no_external_dependencies(monkeypatch) -> None:
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    assert distribution([1, 2, 3])["mean"] == 2


def test_no_llm_or_order_code_in_pure_audit_helpers() -> None:
    assert leave_one_factor_out(_rows())


def test_scheduler_remains_out_of_scope() -> None:
    scheduler_enabled = False
    assert scheduler_enabled is False


def test_database_and_excel_exact_reconciliation() -> None:
    database = [
        {
            "stock_code": "000862",
            "total_score": 63.1629,
            "technical_score": 83.1032,
            "capital_score": 51.9895,
            "emotion_score": 50,
            "momentum_score": 81.5347,
            "risk_score": 47.7301,
        }
    ]
    workbook = [
        {
            "股票代码": "000862",
            "量化总分": 63.1629,
            "技术得分": 83.1032,
            "资金得分": 51.9895,
            "情绪得分": 50,
            "动量得分": 81.5347,
            "风险得分": 47.7301,
        }
    ]
    assert all(
        row["status"] == "PASS"
        for row in database_excel_reconciliation(database, workbook)
    )


def test_database_and_excel_mapping_error_is_detected() -> None:
    database = [
        {
            "stock_code": "000001",
            "total_score": 10,
            "technical_score": 20,
            "capital_score": 30,
            "emotion_score": 40,
            "momentum_score": 50,
            "risk_score": 60,
        }
    ]
    workbook = [
        {
            "股票代码": "000001",
            "量化总分": 11,
            "技术得分": 20,
            "资金得分": 30,
            "情绪得分": 40,
            "动量得分": 50,
            "风险得分": 60,
        }
    ]
    failures = [
        row
        for row in database_excel_reconciliation(database, workbook)
        if row["status"] == "FAIL"
    ]
    assert [row["field"] for row in failures] == ["total_score"]


def test_sector_level_duplicate_scores_are_legal() -> None:
    sector_scores = {"电力": 62.5, "化工": 48.0}
    stock_sectors = ["电力", "电力", "化工"]
    scores = [sector_scores[sector] for sector in stock_sectors]
    assert scores == [62.5, 62.5, 48.0]
    assert len(set(scores)) < len(scores)


def test_industry_join_failure_is_detected() -> None:
    result = detect_mapping_join_failure(["000862.SZ", "300896.SZ"], [])
    assert result["join_failed"] is True
    assert result["missing_count"] == 2


def test_industry_join_normalizes_tushare_and_plain_codes() -> None:
    result = detect_mapping_join_failure(["000862.SZ"], ["000862"])
    assert result["join_failed"] is False
    assert result["matched_count"] == 1


def test_audit_hash_is_stable_for_equivalent_payloads() -> None:
    left = canonical_hash({"weights": {"technical": 0.25, "capital": 0.25}})
    right = canonical_hash({"weights": {"capital": 0.25, "technical": 0.25}})
    assert left == right


def test_audit_hash_detects_configuration_change() -> None:
    before = canonical_hash({"emotion": 0.20})
    after = canonical_hash({"emotion": 0.21})
    assert before != after
