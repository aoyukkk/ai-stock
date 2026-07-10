from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from temporal.calendar import TradeCalendarService
from temporal.context import TemporalContextFactory
from temporal.gate import TemporalConsistencyGate
from temporal.schemas import DatasetWatermark, RunDataManifest, RunMode, TemporalStatus


SH = ZoneInfo("Asia/Shanghai")
OPEN = [date(2026, 7, 3), date(2026, 7, 6), date(2026, 7, 7), date(2026, 7, 8), date(2026, 7, 9), date(2026, 7, 10), date(2026, 7, 13)]


def _wm(name, day, complete=True, ratio=1.0):
    return DatasetWatermark(dataset_name=name, requested_trade_date=day, latest_trade_date=day,
        fetched_at=datetime(2026, 7, 10, tzinfo=timezone.utc), row_count=100, expected_count=100,
        coverage_ratio=ratio, is_complete=complete, is_stale=False, schema_version="v1", source_status="CACHE")


def _manifest(context, required):
    return RunDataManifest(id="m1", run_id=context.run_id, run_mode=context.run_mode,
        decision_time=context.decision_time, base_market_trade_date=context.base_market_trade_date,
        target_trade_date=context.target_trade_date, news_cutoff_time=context.news_cutoff_time,
        fundamental_cutoff_time=context.fundamental_cutoff_time, required_dataset_watermarks=required,
        temporal_status=TemporalStatus.BLOCKED, actionable=False, created_at=context.created_at)


def test_calendar_skips_weekend_and_sessions_are_shanghai_aware():
    cal = TradeCalendarService(open_dates=OPEN)
    assert cal.next_open_trade_date(date(2026, 7, 10)) == date(2026, 7, 13)
    assert cal.previous_open_trade_date(date(2026, 7, 6)) == date(2026, 7, 3)
    assert cal.market_session(datetime(2026, 7, 10, 8, 30, tzinfo=SH)) == "PRE_MARKET"
    assert cal.market_session(datetime(2026, 7, 10, 10, 0, tzinfo=SH)) == "MORNING_SESSION"
    assert cal.market_session(datetime(2026, 7, 10, 16, 0, tzinfo=SH)) == "POST_MARKET"


def test_naive_decision_time_rejected():
    with pytest.raises(ValueError):
        TemporalContextFactory(TradeCalendarService(open_dates=OPEN)).create(
            RunMode.POST_MARKET_FINAL, datetime(2026, 7, 10, 16)
        )


def test_post_market_final_blocks_mixed_dates_and_low_coverage():
    context = TemporalContextFactory(TradeCalendarService(open_dates=OPEN)).create(
        RunMode.POST_MARKET_FINAL, datetime(2026, 7, 10, 20, tzinfo=SH)
    )
    required = [_wm("daily", date(2026, 7, 10)), _wm("daily_basic", date(2026, 7, 10), complete=False, ratio=.8), _wm("moneyflow", date(2026, 7, 9))]
    result = TemporalConsistencyGate().evaluate(context, _manifest(context, required))
    assert result.temporal_status is TemporalStatus.BLOCKED
    assert "DAILY_BASIC_COVERAGE_INCOMPLETE" in result.block_reasons
    assert "MONEYFLOW_TRADE_DATE_MISMATCH" in result.block_reasons


def test_pre_market_legal_cross_date_alignment_passes():
    context = TemporalContextFactory(TradeCalendarService(open_dates=OPEN)).create(
        RunMode.PRE_MARKET_RECHECK, datetime(2026, 7, 10, 9, 10, tzinfo=SH),
        base_market_trade_date=date(2026, 7, 9), target_trade_date=date(2026, 7, 10)
    )
    required = [_wm(name, date(2026, 7, 9)) for name in ("daily", "daily_basic", "moneyflow")]
    required += [_wm(name, date(2026, 7, 10)) for name in ("stk_limit", "adj_factor")]
    result = TemporalConsistencyGate().evaluate(context, _manifest(context, required))
    assert result.temporal_status is TemporalStatus.PASS
    assert result.actionable is True


def test_intraday_without_realtime_is_blocked():
    context = TemporalContextFactory(TradeCalendarService(open_dates=OPEN)).create(
        RunMode.INTRADAY_MONITOR, datetime(2026, 7, 10, 10, tzinfo=SH)
    )
    result = TemporalConsistencyGate().evaluate(context, _manifest(context, []))
    assert result.actionable is False
    assert "REALTIME_MARKET_DATA_REQUIRED" in result.block_reasons


def test_intraday_post_market_final_cannot_use_previous_close_as_current_target():
    context = TemporalContextFactory(TradeCalendarService(open_dates=OPEN)).create(
        RunMode.POST_MARKET_FINAL, datetime(2026, 7, 10, 12, tzinfo=SH)
    )
    required = [_wm(name, date(2026, 7, 9)) for name in ("daily", "daily_basic", "moneyflow")]
    result = TemporalConsistencyGate().evaluate(context, _manifest(context, required))
    assert result.temporal_status is TemporalStatus.BLOCKED
    assert "CURRENT_TRADE_DATE_NOT_COMPLETE" in result.block_reasons
