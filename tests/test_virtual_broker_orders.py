from decimal import Decimal

import database.models  # noqa: F401
from database.base import Base
from database.session import create_engine_from_url, get_session
from trading.virtual_broker import VirtualBroker


def make_broker():
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = get_session(engine)
    return engine, session, VirtualBroker(session)


def test_create_account_and_submit_buy_order_updates_cash_and_position() -> None:
    engine, session, broker = make_broker()
    try:
        account = broker.create_account()
        order = broker.buy(account.account_id, "000001", Decimal("8.00"), 100, reason="unit test")
        updated = broker.get_account(account.account_id)
        positions = broker.get_positions(account.account_id)
        trades = broker.get_trades(account.account_id)

        assert order.status == "FILLED"
        assert order.filled_quantity == 100
        assert updated.cash < account.cash
        assert positions[0].quantity == 100
        assert positions[0].available_quantity == 0
        assert trades[0].reason and "virtual trading simulation" in trades[0].reason
    finally:
        session.close()
        engine.dispose()


def test_cancel_pending_order() -> None:
    engine, session, broker = make_broker()
    try:
        account = broker.create_account()
        order = broker.buy(account.account_id, "000001", Decimal("7.00"), 100, reason="pending")

        assert order.status == "PENDING"
        cancelled = broker.cancel_order(order.order_id, reason="cancel test")
        assert cancelled.status == "CANCELLED"
    finally:
        session.close()
        engine.dispose()


def test_reprice_pending_order() -> None:
    engine, session, broker = make_broker()
    try:
        account = broker.create_account()
        order = broker.buy(account.account_id, "000001", Decimal("7.00"), 100, reason="pending")

        repriced = broker.reprice_order(order.order_id, Decimal("7.50"), reason="reprice test")
        assert repriced.status == "REPRICED"
        assert repriced.order_price == Decimal("7.5000")
    finally:
        session.close()
        engine.dispose()
