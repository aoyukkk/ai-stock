from __future__ import annotations

from datetime import datetime, time
from uuid import uuid4

from temporal.calendar import SHANGHAI, TradeCalendarService, ensure_shanghai
from temporal.schemas import RunMode, RunTemporalContext


class TemporalContextFactory:
    def __init__(self, calendar: TradeCalendarService | None = None) -> None:
        self.calendar = calendar or TradeCalendarService()

    def create(
        self,
        run_mode: RunMode,
        decision_time: datetime | None = None,
        *,
        base_market_trade_date=None,
        target_trade_date=None,
        allow_provisional: bool = False,
    ) -> RunTemporalContext:
        now = ensure_shanghai(decision_time or datetime.now(SHANGHAI))
        latest = self.calendar.latest_completed_trade_date(now)
        base = base_market_trade_date or latest
        if run_mode is RunMode.HISTORICAL_REPLAY and (base_market_trade_date is None or target_trade_date is None):
            raise ValueError("historical replay requires explicit base and target trade dates")
        target = target_trade_date or self.calendar.next_open_trade_date(base)
        cutoff = datetime.combine(target, time(9, 25), SHANGHAI) if run_mode is RunMode.PRE_MARKET_RECHECK else now
        return RunTemporalContext(
            run_id=f"temporal-{uuid4().hex}", run_mode=run_mode, decision_time=now,
            market_session=self.calendar.market_session(now), base_market_trade_date=base,
            target_trade_date=target, latest_completed_trade_date=latest,
            news_cutoff_time=cutoff, fundamental_cutoff_time=now,
            allow_provisional=allow_provisional, created_at=now,
        )
