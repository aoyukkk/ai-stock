from __future__ import annotations

from scripts.build_weekly_sector_review import _stats


def test_weekly_sector_review_rates_exclude_pending_and_ambiguous() -> None:
    rows = [
        {
            "eligible": True,
            "result_class": "STRONG_SUCCESS",
            "current_net_return": 0.1,
            "mfe": 0.2,
        },
        {
            "eligible": True,
            "result_class": "STABLE_SUCCESS",
            "current_net_return": 0.01,
            "mfe": 0.03,
        },
        {
            "eligible": True,
            "result_class": "OPPORTUNITY_HIT_GIVEBACK",
            "current_net_return": -0.01,
            "mfe": 0.06,
        },
        {
            "eligible": True,
            "result_class": "FAIL",
            "current_net_return": -0.03,
            "mfe": 0.01,
        },
        {"eligible": False, "result_class": "PENDING"},
        {"eligible": False, "result_class": "PATH_AMBIGUOUS"},
    ]
    result = _stats(rows)
    assert result["mature_count"] == 4
    assert result["current_profit_rate"] == 0.5
    assert result["opportunity_hit_rate"] == 0.75
    assert result["pending_or_excluded"] == 2
