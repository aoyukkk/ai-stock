from datetime import date
from decimal import Decimal

import database.models  # noqa: F401
from database.base import Base
from database.session import create_engine_from_url, get_session
from trading.virtual_broker import VirtualBroker


def test_tplus1_available_quantity_after_settlement() -> None:
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = get_session(engine)
    broker = VirtualBroker(session)

    try:
        account = broker.create_account()
        buy_order = broker.buy(account.account_id, "000001", Decimal("8.00"), 100, reason="tplus1")
        position = broker.get_positions(account.account_id)[0]

        assert buy_order.status == "FILLED"
        assert position.quantity == 100
        assert position.available_quantity == 0

        blocked_sell = broker.sell(account.account_id, "000001", Decimal("8.00"), 100, reason="same day sell")
        assert blocked_sell.status == "FAILED"
        assert "T+1" in (blocked_sell.fail_reason or "")

        broker.settle_t_plus_one(account.account_id, date(2026, 1, 6))
        settled = broker.get_positions(account.account_id)[0]
        assert settled.available_quantity == 100

        sell_order = broker.sell(account.account_id, "000001", Decimal("8.00"), 100, reason="next day sell")
        assert sell_order.status == "FILLED"
    finally:
        session.close()
        engine.dispose()
