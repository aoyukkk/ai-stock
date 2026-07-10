from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from datasource.tushare_provider import TushareMarketDataProvider


SHANGHAI = ZoneInfo("Asia/Shanghai")


class TradeCalendarService:
    def __init__(self, provider=None, open_dates: list[date] | None = None) -> None:
        self.provider = provider or TushareMarketDataProvider()
        self._open_dates = sorted(set(open_dates or []))

    def open_dates(self, start: date, end: date) -> list[date]:
        if self._open_dates:
            return [item for item in self._open_dates if start <= item <= end]
        values = self.provider.get_open_trade_dates(start.isoformat(), end.isoformat())
        return [datetime.strptime(item, "%Y%m%d").date() for item in values]

    def previous_open_trade_date(self, day: date) -> date:
        dates = self.open_dates(day - timedelta(days=20), day - timedelta(days=1))
        if not dates:
            raise ValueError("previous open trade date unavailable")
        return dates[-1]

    def next_open_trade_date(self, day: date) -> date:
        dates = self.open_dates(day + timedelta(days=1), day + timedelta(days=20))
        if not dates:
            raise ValueError("next open trade date unavailable")
        return dates[0]

    def is_open_trade_date(self, day: date) -> bool:
        return day in self.open_dates(day, day)

    def market_session(self, at: datetime) -> str:
        local = ensure_shanghai(at)
        if not self.is_open_trade_date(local.date()):
            return "NON_TRADING_DAY"
        clock = local.time()
        if clock < time(9, 15): return "PRE_MARKET"
        if clock < time(9, 30): return "OPENING_AUCTION"
        if clock <= time(11, 30): return "MORNING_SESSION"
        if clock < time(13, 0): return "MIDDAY_BREAK"
        if clock <= time(15, 0): return "AFTERNOON_SESSION"
        return "POST_MARKET"

    def latest_completed_trade_date(self, at: datetime) -> date:
        local = ensure_shanghai(at)
        if self.is_open_trade_date(local.date()) and local.time() >= time(15, 0):
            return local.date()
        return self.previous_open_trade_date(local.date())


def ensure_shanghai(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timezone-aware datetime required")
    return value.astimezone(SHANGHAI)
