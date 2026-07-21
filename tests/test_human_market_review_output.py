from __future__ import annotations

from market_review.human_excel import _market_overview_subtitle
from scripts.build_human_market_review import _markdown


def _bundle() -> dict:
    return {
        "run": {
            "trade_date": "2026-07-16",
            "base_case_probability": 58,
            "bull_case_probability": 20,
            "bear_case_probability": 22,
        },
        "snapshot": {
            "breadth": {
                "advancing_count": 2499,
                "declining_count": 2861,
                "flat_count": 164,
                "equal_weight_return": -0.006,
            },
            "turnover": {"total_amount": 2_418_945_000_000, "change_ratio": -0.0651},
            "limit_structure": {"limit_up_count": 48, "limit_down_count": 41},
            "industries": [
                {"sector_name": "影视音像"},
                {"sector_name": "旅游服务"},
                {"sector_name": "白酒"},
                {"sector_name": "半导体"},
                {"sector_name": "矿物制品"},
                {"sector_name": "黄金"},
            ],
        },
        "drivers": [
            {"title": "市场宽度与等权表现", "explanation": "下跌家数多于上涨家数。"},
        ],
    }


def test_market_overview_matches_negative_breadth_and_contracting_turnover():
    subtitle = _market_overview_subtitle(_bundle()["snapshot"])

    assert subtitle == "全A个股整体偏弱，成交缩量，行业轮动明显。"


def test_market_markdown_has_structural_causes_without_supplement():
    markdown = _markdown(_bundle(), {})

    assert "全A个股整体偏弱" in markdown
    assert "市场宽度与等权表现" in markdown
    assert "领涨为 影视音像、旅游服务、白酒" in markdown
