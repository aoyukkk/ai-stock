from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from market_forecast.data_loader import MarketForecastDataLoader
from market_forecast.data_validation import MarketDataValidationError, validate_market_data
from tests.market_forecast_test_utils import FakeTushareProvider, synthetic_calendar, synthetic_index_frame


def test_data_loader_uses_incremental_parquet_cache_without_duplicates(tmp_path) -> None:
    frame = synthetic_index_frame("2020-01-02", periods=7)
    calendar = synthetic_calendar(frame)
    provider = FakeTushareProvider(frame, calendar)
    loader = MarketForecastDataLoader(
        provider,
        cache_root=tmp_path,
        now=lambda: datetime(2020, 1, 10, 16, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    first = loader.load(index_code="000001.SH", start_date="20200101", end_date="auto")
    first_call_count = len(provider.calls)
    second = loader.load(index_code="000001.SH", start_date="20200101", end_date="auto")

    assert first.quality.report["status"] == "PASS"
    assert len(second.quality.frame) == len(frame)
    assert second.quality.frame["trade_date"].is_unique
    assert len(provider.calls) == first_call_count
    assert (tmp_path / "index_daily" / "000001.SH.parquet").exists()


def test_data_validation_rejects_missing_open_trade_date() -> None:
    frame = synthetic_index_frame(periods=10)
    calendar = synthetic_calendar(frame)
    missing = frame.drop(index=4).reset_index(drop=True)

    try:
        validate_market_data(
            missing,
            calendar,
            index_code="000001.SH",
            expected_latest_trade_date=pd.Timestamp(frame["trade_date"].iloc[-1]),
        )
    except MarketDataValidationError as exc:
        assert "MISSING_OPEN_TRADE_DATES" in str(exc)
    else:
        raise AssertionError("missing open date must fail")
