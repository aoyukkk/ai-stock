from datetime import date, timedelta

import pytest

from review.performance_calculators import PortfolioReturnCalculator, ReturnCalculator, compound_return
from review.performance_schemas import MarketBar, MemberSnapshot


def test_next_open_and_compound_return_are_not_simple_sum() -> None:
    member = MemberSnapshot(1, "000001.SZ", date(2026, 7, 3), 0.6)
    bars = {
        date(2026, 7, 6): MarketBar("000001.SZ", date(2026, 7, 6), 10, 11, 9.9, 11, 10, 10),
        date(2026, 7, 7): MarketBar("000001.SZ", date(2026, 7, 7), 11, 12.2, 10.8, 12.1, 11, 10),
    }
    rows = ReturnCalculator().calculate(member, bars, sorted(bars), "NEXT_OPEN")
    assert rows[0]["daily_return"] == pytest.approx(0.1)
    assert rows[1]["daily_return"] == pytest.approx(0.1)
    assert rows[1]["cumulative_return"] == pytest.approx(0.21)
    assert rows[1]["cumulative_return"] != pytest.approx(sum(row["daily_return"] for row in rows))


def test_signal_close_drawdown_suspension_and_missing() -> None:
    member = MemberSnapshot(1, "000001.SZ", date(2026, 7, 3), 1.0)
    bars = {
        date(2026, 7, 3): MarketBar("000001.SZ", date(2026, 7, 3), 10, 10, 10, 10, 9.5),
        date(2026, 7, 6): MarketBar("000001.SZ", date(2026, 7, 6), 10, 11, 9, 11, 10),
        date(2026, 7, 7): MarketBar("000001.SZ", date(2026, 7, 7), None, None, None, 11, 11, suspended=True),
    }
    dates = [date(2026, 7, 6), date(2026, 7, 7), date(2026, 7, 8)]
    rows = ReturnCalculator().calculate(member, bars, dates, "SIGNAL_CLOSE")
    assert rows[0]["daily_return"] == pytest.approx(0.1)
    assert rows[1]["daily_return"] == 0
    assert rows[1]["data_status"] == "SUSPENDED_CARRY_FORWARD"
    assert rows[2]["daily_return"] is None
    assert rows[2]["data_status"] == "UNKNOWN_MISSING"
    assert rows[2]["cumulative_return"] == pytest.approx(0.1)


def test_equal_and_position_weighted_portfolios_and_zero_weight_status() -> None:
    members = [MemberSnapshot(1, "000001.SZ", date(2026, 7, 3), 0.75), MemberSnapshot(2, "000002.SZ", date(2026, 7, 3), 0.25)]
    rows = [
        {"cohort_member_id": 1, "evaluation_trade_date": date(2026, 7, 6), "holding_day": 1, "daily_return": 0.1, "data_status": "AVAILABLE"},
        {"cohort_member_id": 2, "evaluation_trade_date": date(2026, 7, 6), "holding_day": 1, "daily_return": -0.1, "data_status": "AVAILABLE"},
    ]
    equal = PortfolioReturnCalculator().calculate(1, members, rows, "EQUAL_WEIGHT")
    weighted = PortfolioReturnCalculator().calculate(1, members, rows, "SUGGESTED_POSITION_WEIGHT")
    assert equal[0]["daily_return"] == pytest.approx(0)
    assert weighted[0]["daily_return"] == pytest.approx(0.05)
    zero_members = [MemberSnapshot(1, "000001.SZ", date(2026, 7, 3), 0), MemberSnapshot(2, "000002.SZ", date(2026, 7, 3), 0)]
    assert PortfolioReturnCalculator().calculate(1, zero_members, rows, "SUGGESTED_POSITION_WEIGHT")[0]["status"] == "POSITION_WEIGHT_UNAVAILABLE"
    assert compound_return([0.1, -0.1]) == pytest.approx(-0.01)


def test_five_selection_day_mock_covers_sources_up_down_suspension_and_missing() -> None:
    sources = ["LLM", "MANUAL", "BOTH"]
    mock_cohorts = []
    for offset in range(5):
        selection_day = date(2026, 6, 29) + timedelta(days=offset)
        members = [MemberSnapshot(offset * 3 + index + 1, f"00000{index + 1}.SZ", selection_day, [0.5, 0.3, 0.2][index]) for index in range(3)]
        evaluation_day = date(2026, 7, 6)
        rows = [
            {"cohort_member_id": members[0].member_id, "evaluation_trade_date": evaluation_day, "holding_day": 1, "daily_return": 0.03, "data_status": "AVAILABLE"},
            {"cohort_member_id": members[1].member_id, "evaluation_trade_date": evaluation_day, "holding_day": 1, "daily_return": -0.02, "data_status": "AVAILABLE"},
            {"cohort_member_id": members[2].member_id, "evaluation_trade_date": evaluation_day, "holding_day": 1, "daily_return": 0.0 if offset % 2 == 0 else None, "data_status": "SUSPENDED_CARRY_FORWARD" if offset % 2 == 0 else "UNKNOWN_MISSING"},
        ]
        equal = PortfolioReturnCalculator().calculate(offset + 1, members, rows, "EQUAL_WEIGHT")
        weighted = PortfolioReturnCalculator().calculate(offset + 1, members, rows, "SUGGESTED_POSITION_WEIGHT")
        mock_cohorts.append({"selection_day": selection_day, "sources": sources, "equal": equal, "weighted": weighted})
    assert len(mock_cohorts) == 5
    assert all(item["equal"] and item["weighted"] for item in mock_cohorts)
    assert any(item["equal"][0]["suspended_count"] == 1 for item in mock_cohorts)
    assert any(item["equal"][0]["missing_count"] == 1 for item in mock_cohorts)
