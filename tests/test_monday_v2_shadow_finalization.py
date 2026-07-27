from __future__ import annotations

from scripts.finalize_monday_v2_shadow import (
    DISCLAIMER,
    apply_regime_cap,
    build_theme_membership,
)


def _row(code: str, rank: int, industry: str = "行业A") -> dict:
    return {
        "stock_code": code,
        "stock_name": code,
        "rank": rank,
        "total_score": 90 - rank,
        "risk_score": 80,
        "level_one_sector": industry,
    }


def _pro(code: str, rank: int, score: float = 80) -> dict:
    return {
        "stock_code": code,
        "quant_rank": rank,
        "pro_rank": rank,
        "pro_score": score,
        "priority": "HIGH",
        "manual_review_priority": "LOW",
        "data_conflict": False,
        "flash_score": 75,
        "flash_decision": "ADVANCE",
        "final_summary": "ok",
        "key_strengths": [],
        "key_risks": [],
    }


def test_risk_off_cap_is_two_and_same_industry_one() -> None:
    sources = {
        "000001": _row("000001", 1, "行业A"),
        "000002": _row("000002", 2, "行业A"),
        "000003": _row("000003", 3, "行业B"),
    }
    themes = {
        "000001": {
            "binding_cluster": "主题A",
            "all_theme_clusters": "主题A",
            "retained": "",
            "concentration_reason": "",
        },
        "000002": {
            "binding_cluster": "主题B",
            "all_theme_clusters": "主题B",
            "retained": "",
            "concentration_reason": "",
        },
        "000003": {
            "binding_cluster": "主题C",
            "all_theme_clusters": "主题C",
            "retained": "",
            "concentration_reason": "",
        },
    }
    watch, active, removals = apply_regime_cap(
        [_pro("000001", 1), _pro("000002", 2), _pro("000003", 3)],
        sources,
        themes,
    )
    assert len(watch) == 3
    assert [row["stock_code"] for row in active] == ["000001", "000003"]
    assert removals == 1
    assert "SAME_INDUSTRY" in watch[1]["concentration_reason"]


def test_risk_off_same_theme_one() -> None:
    sources = {
        "000001": _row("000001", 1, "行业A"),
        "000002": _row("000002", 2, "行业B"),
    }
    themes = {
        code: {
            "binding_cluster": "军工装备",
            "all_theme_clusters": "军工装备",
            "retained": "",
            "concentration_reason": "",
        }
        for code in sources
    }
    watch, active, removals = apply_regime_cap(
        [_pro("000001", 1), _pro("000002", 2)], sources, themes
    )
    assert len(active) == 1
    assert removals == 1
    assert "SAME_THEME" in watch[1]["concentration_reason"]


def test_theme_output_has_required_contract() -> None:
    rows, by_stock = build_theme_membership(
        [_row("300008", 1, "船舶"), _row("002414", 2, "电子元件")]
    )
    assert len(rows) == 2
    assert rows[0]["disclaimer"] == DISCLAIMER
    assert {"all_theme_clusters", "binding_cluster", "retained", "concentration_reason"} <= set(
        rows[0]
    )
    assert by_stock["300008"]["binding_cluster"] == "军工装备"
