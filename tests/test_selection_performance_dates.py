from datetime import date

import pytest

from review.performance_schemas import PerformanceRequest
from temporal.calendar import TradeCalendarService


def test_default_is_five_trading_days_and_weekend_is_not_in_calendar() -> None:
    request = PerformanceRequest(evaluation_end_date=date(2026, 7, 10))
    assert request.lookback_value == 5
    calendar = TradeCalendarService(open_dates=[date(2026, 7, 6), date(2026, 7, 7), date(2026, 7, 8), date(2026, 7, 9), date(2026, 7, 10)])
    assert len(calendar.open_dates(date(2026, 7, 4), date(2026, 7, 10))) == 5
    assert not calendar.is_open_trade_date(date(2026, 7, 5))


@pytest.mark.parametrize("value", [1, 10, 20, 60, 120])
def test_supported_lookbacks(value: int) -> None:
    assert PerformanceRequest(evaluation_end_date=date(2026, 7, 10), lookback_value=value).validate().lookback_value == value


def test_invalid_ranges_and_future_selection_are_rejected() -> None:
    with pytest.raises(ValueError, match="LOOKBACK_OUT_OF_RANGE"):
        PerformanceRequest(evaluation_end_date=date(2026, 7, 10), lookback_value=121).validate()
    with pytest.raises(ValueError, match="START_DATE_AFTER_END_DATE"):
        PerformanceRequest(evaluation_end_date=date(2026, 7, 10), lookback_unit="CUSTOM", start_selection_date=date(2026, 7, 9), end_selection_date=date(2026, 7, 8)).validate()
    with pytest.raises(ValueError, match="SELECTION_DATE_AFTER_EVALUATION_END"):
        PerformanceRequest(evaluation_end_date=date(2026, 7, 10), lookback_unit="CUSTOM", start_selection_date=date(2026, 7, 10), end_selection_date=date(2026, 7, 13)).validate()
