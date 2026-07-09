from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select

import database.models  # noqa: F401
from database.base import Base
from database.models.order_plan import OrderPlan, OrderReassessmentLog
from database.models.trading import TradeOrder, TradeRecord
from database.session import create_engine_from_url, get_session
from datasource.schemas import LimitPriceInfo, NewsItem, PreMarketAuctionInfo, RealtimeQuote
from recheck.pre_market import PreMarketRecheckEngine


class StubDataSource:
    def __init__(self, auction_change: str = "1.0", news_importance: str = "0") -> None:
        self.auction_change = Decimal(auction_change)
        self.news_importance = Decimal(news_importance)

    def get_realtime_quotes(self, stock_codes):
        return [
            RealtimeQuote(
                stock_code=stock_codes[0],
                name="Mock",
                current_price=Decimal("10.00"),
                change_percent=Decimal("1.00"),
                volume=1000000,
                amount=Decimal("10000000"),
                quote_time=datetime(2026, 1, 5, 9, 20, tzinfo=timezone.utc),
            )
        ]

    def get_pre_market_auction(self, stock_code, trade_date):
        return PreMarketAuctionInfo(
            stock_code=stock_code,
            trade_date=trade_date,
            auction_price=Decimal("10.00"),
            auction_volume=100000,
            auction_amount=Decimal("1000000"),
            auction_change_percent=self.auction_change,
            auction_strength_score=Decimal("60"),
        )

    def get_limit_price(self, stock_code, trade_date):
        return LimitPriceInfo(
            stock_code=stock_code,
            trade_date=trade_date,
            previous_close=Decimal("10.00"),
            limit_up_price=Decimal("11.00"),
            limit_down_price=Decimal("9.00"),
        )

    def get_latest_news(self, limit=20):
        if self.news_importance <= 0:
            return []
        return [
            NewsItem(
                title="Major negative mock news",
                content="negative",
                source="mock",
                publish_time=datetime(2026, 1, 5, 8, 0, tzinfo=timezone.utc),
                importance=self.news_importance,
                sentiment="NEGATIVE",
                related_stocks=["000001"],
            )
        ]


def setup_plan(session) -> OrderPlan:
    plan = OrderPlan(
        stock_code="000001",
        plan_date=date(2026, 1, 5),
        plan_session="POST_MARKET",
        side="BUY",
        strategy_type="BALANCED",
        recommended_price=Decimal("10.00"),
        status="DRAFT",
    )
    session.add(plan)
    session.commit()
    return plan


def run_case(stub: StubDataSource):
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = get_session(engine)
    try:
        plan = setup_plan(session)
        result = PreMarketRecheckEngine(session=session, data_source_service=stub).recheck_order_plan(plan.id)
        logs = session.scalars(select(OrderReassessmentLog)).all()
        trade_orders = session.scalars(select(TradeOrder)).all()
        trade_records = session.scalars(select(TradeRecord)).all()
        return result, logs, trade_orders, trade_records
    finally:
        session.close()
        engine.dispose()


def test_high_open_outputs_cancel_or_watch_only() -> None:
    result, logs, trade_orders, trade_records = run_case(StubDataSource(auction_change="7"))

    assert result.action in {"CANCEL", "WATCH_ONLY"}
    assert len(logs) == 1
    assert trade_orders == []
    assert trade_records == []


def test_low_open_outputs_reprice_or_watch_only() -> None:
    result, *_ = run_case(StubDataSource(auction_change="-5"))

    assert result.action in {"REPRICE", "WATCH_ONLY"}
    assert result.new_recommended_price is not None


def test_major_negative_news_outputs_block() -> None:
    result, *_ = run_case(StubDataSource(news_importance="90"))

    assert result.action == "BLOCK"
    assert result.alert_events


def test_normal_case_outputs_keep() -> None:
    result, *_ = run_case(StubDataSource(auction_change="1"))

    assert result.action == "KEEP"
