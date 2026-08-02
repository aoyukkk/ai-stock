from datetime import date

from services.ranking_evaluation.full_universe_service import _july_31_data_used


def test_july_31_usage_flag_reflects_maximum_market_date() -> None:
    maximum_market_date = date(2026, 7, 31)

    assert _july_31_data_used(maximum_market_date) is True
    assert _july_31_data_used(date(2026, 7, 30)) is False
    assert _july_31_data_used(None) is False
