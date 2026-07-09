from sqlalchemy import select

import database.models  # noqa: F401
from database.base import Base
from database.models.trading import Position, TradeOrder, TradeRecord, TradingAccount
from database.session import create_engine_from_url, get_session
from trading.service import VirtualTradingService


def test_run_order_plans_writes_ai_simulation_records_only() -> None:
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = get_session(engine)

    try:
        service = VirtualTradingService(session=session)
        report = service.run_order_plans(input_top_n=2)

        accounts = session.scalars(select(TradingAccount)).all()
        orders = session.scalars(select(TradeOrder)).all()
        trades = session.scalars(select(TradeRecord)).all()
        positions = session.scalars(select(Position)).all()

        assert report.summary["virtual_only"] is True
        assert report.summary["real_trading_enabled"] is False
        assert len(accounts) == 1
        assert accounts[0].type == "AI_SIMULATION"
        assert orders
        assert trades
        assert positions
        assert all((trade.reason or "").startswith("virtual trading simulation") for trade in trades)
    finally:
        session.close()
        engine.dispose()


def test_service_query_methods_use_default_ai_account() -> None:
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = get_session(engine)

    try:
        service = VirtualTradingService(session=session)
        account = service.create_default_ai_account()

        assert account.name == "AI Simulation"
        assert service.get_account_summary().account_id == account.account_id
        assert service.get_orders() == []
        assert service.get_positions() == []
        assert service.get_trades() == []
    finally:
        session.close()
        engine.dispose()
