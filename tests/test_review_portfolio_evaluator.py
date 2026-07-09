from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import database.models  # noqa: F401
from database.base import Base
from database.models.trading import Position, TradeOrder, TradingAccount
from database.session import create_engine_from_url, get_session
from datasource.schemas import RealtimeQuote
from review.portfolio_evaluator import PortfolioEvaluator


class StubDataSource:
    def get_realtime_quotes(self, stock_codes):
        return [
            RealtimeQuote(
                stock_code=code,
                name="Mock",
                current_price=Decimal("11.00"),
                change_percent=Decimal("1.00"),
                volume=1000000,
                amount=Decimal("11000000"),
                quote_time=datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc),
            )
            for code in stock_codes
        ]


def test_portfolio_evaluator_calculates_ai_simulation_metrics() -> None:
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = get_session(engine)

    try:
        account = TradingAccount(
            name="AI Simulation",
            type="AI_SIMULATION",
            cash=Decimal("9000.00"),
            total_asset=Decimal("10000.00"),
            initial_cash=Decimal("10000.00"),
        )
        session.add(account)
        session.flush()
        session.add(
            Position(
                account_id=account.id,
                stock_code="000001",
                quantity=100,
                cost_price=Decimal("10.00"),
                available_quantity=100,
                buy_date=date(2026, 1, 5),
            )
        )
        session.add(
            TradeOrder(
                account_id=account.id,
                stock_code="000001",
                action="BUY",
                order_price=Decimal("10.00"),
                order_quantity=100,
                filled_quantity=100,
                status="FILLED",
                submit_time=datetime(2026, 1, 5, 9, 30, tzinfo=timezone.utc),
            )
        )
        session.commit()

        result = PortfolioEvaluator(
            session=session,
            data_source_service=StubDataSource(),
        ).evaluate_account(account.id, date(2026, 1, 5))

        assert result.account_type == "AI_SIMULATION"
        assert result.profit_loss == Decimal("100.0000")
        assert result.win_rate == Decimal("100.0000")
        assert result.order_fill_rate == Decimal("100.0000")
    finally:
        session.close()
        engine.dispose()


def test_portfolio_evaluator_handles_no_trades() -> None:
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = get_session(engine)

    try:
        result = PortfolioEvaluator(
            session=session,
            data_source_service=StubDataSource(),
        ).evaluate_ai_simulation(date(2026, 1, 5))

        assert result.account_type == "AI_SIMULATION"
        assert result.order_count == 0
        assert result.order_fill_rate == Decimal("0")
    finally:
        session.close()
        engine.dispose()
