from __future__ import annotations

from scripts.build_weekly_recommendation_review import (
    _aggregate,
    _classify,
    _deduplicate_overview,
    _evaluate,
    _meets_recommendation_threshold,
    _normalize_ts_code,
)


def test_weekly_review_normalizes_plain_codes_for_tushare_joins() -> None:
    assert _normalize_ts_code("000538") == "000538.SZ"
    assert _normalize_ts_code("603288") == "603288.SH"
    assert _normalize_ts_code("920651") == "920651.BJ"
    assert _normalize_ts_code("000538.SZ") == "000538.SZ"


def test_weekly_review_result_class_contract() -> None:
    assert _classify(0.05, 0.01) == "STRONG_SUCCESS"
    assert _classify(0.049, 0.01) == "STABLE_SUCCESS"
    assert _classify(0.05, 0.0) == "OPPORTUNITY_HIT_GIVEBACK"
    assert _classify(0.049, 0.0) == "FAIL"


def test_weekly_review_recommendation_scope_uses_pro_score_ge_60() -> None:
    assert _meets_recommendation_threshold({"pro_score": 60, "quant_score": 10}, 60)
    assert not _meets_recommendation_threshold(
        {"pro_score": 59.99, "quant_score": 99}, 60
    )
    assert not _meets_recommendation_threshold(
        {"pro_score": None, "quant_score": 99}, 60
    )


def test_weekly_review_excludes_pending_not_tradable_and_ambiguous() -> None:
    rows = [
        {"result_class": "STRONG_SUCCESS", "eligible": True, "current_net_return": 0.1, "mfe": 0.2, "mae": -0.01, "giveback": 0.1, "stop_hit": False},
        {"result_class": "STABLE_SUCCESS", "eligible": True, "current_net_return": 0.01, "mfe": 0.02, "mae": -0.02, "giveback": 0.01, "stop_hit": False},
        {"result_class": "OPPORTUNITY_HIT_GIVEBACK", "eligible": True, "current_net_return": -0.01, "mfe": 0.06, "mae": -0.03, "giveback": 0.07, "stop_hit": True},
        {"result_class": "FAIL", "eligible": True, "current_net_return": -0.02, "mfe": 0.01, "mae": -0.04, "giveback": 0.03, "stop_hit": True},
        {"result_class": "PENDING", "eligible": False, "current_net_return": None, "mfe": None, "mae": None, "giveback": None, "stop_hit": False},
        {"result_class": "NOT_TRADABLE", "eligible": False, "current_net_return": None, "mfe": None, "mae": None, "giveback": None, "stop_hit": False},
        {"result_class": "PATH_AMBIGUOUS", "eligible": False, "current_net_return": 0.1, "mfe": 0.1, "mae": -0.1, "giveback": 0.0, "stop_hit": True},
    ]
    result = _aggregate(rows)
    assert result["eligible_count"] == 4
    assert result["broad_hit_rate"] == 0.75
    assert result["current_profit_rate"] == 0.5
    assert result["opportunity_capture_rate"] == 0.5
    assert result["failure_rate"] == 0.25


def test_weekly_review_pending_retains_monday_plan_prices() -> None:
    plan = {
        "recommended_price": 10.2,
        "max_acceptable_price": 10.5,
        "stop_loss_price": 9.5,
    }
    result = _evaluate(
        recommendation_date="2026-07-24",
        code="000001.SZ",
        plan=plan,
        actual_fill=None,
        daily_by_date={},
        limit_by_date={},
        costs={
            "commission_rate": 0.0003,
            "min_commission": 5.0,
            "stamp_tax_rate": 0.001,
            "slippage_rate": 0.0005,
        },
        pending=True,
    )
    assert result["result_class"] == "PENDING"
    assert result["planned_entry_price"] == 10.2
    assert result["max_acceptable_price"] == 10.5
    assert result["stop_loss_price"] == 9.5
    assert result["daily_returns"] == {}


def test_weekly_review_daily_returns_start_from_legal_entry() -> None:
    result = _evaluate(
        recommendation_date="2026-07-22",
        code="000001.SZ",
        plan={"recommended_price": 10.0},
        actual_fill=None,
        daily_by_date={
            "2026-07-23": {
                "000001.SZ": {
                    "open": 10.0,
                    "high": 10.6,
                    "low": 9.8,
                    "close": 10.5,
                    "vol": 1000,
                }
            },
            "2026-07-24": {
                "000001.SZ": {
                    "open": 10.5,
                    "high": 11.2,
                    "low": 10.4,
                    "close": 11.0,
                    "vol": 1000,
                }
            },
        },
        limit_by_date={},
        costs={
            "commission_rate": 0.0003,
            "min_commission": 5.0,
            "stamp_tax_rate": 0.001,
            "slippage_rate": 0.0005,
        },
        pending=False,
    )
    assert round(result["daily_returns"]["2026-07-23"], 8) == 0.05
    assert round(result["daily_returns"]["2026-07-24"], 8) == round(11 / 10.5 - 1, 8)


def test_weekly_review_overview_deduplicates_and_keeps_first_filled_path() -> None:
    rows = [
        {
            "stock_code": "000001.SZ",
            "recommendation_date": "2026-07-24",
            "entry_status": "PENDING",
            "pro_rank": 1,
        },
        {
            "stock_code": "000001.SZ",
            "recommendation_date": "2026-07-20",
            "entry_status": "FILLED",
            "pro_rank": 3,
            "entry_price": 10.0,
        },
        {
            "stock_code": "000001.SZ",
            "recommendation_date": "2026-07-22",
            "entry_status": "FILLED",
            "pro_rank": 2,
            "entry_price": 11.0,
        },
    ]
    result = _deduplicate_overview(rows)
    assert len(result) == 1
    assert result[0]["first_recommendation_date"] == "2026-07-20"
    assert result[0]["all_recommendation_dates"] == "2026-07-20、2026-07-22、2026-07-24"
    assert result[0]["recommendation_count"] == 3
    assert result[0]["entry_price"] == 10.0
