from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

import database.models  # noqa: F401
from alerts.config import AlertRulesConfig, load_alert_rules_config
from alerts.service import IntradayAlertService
from database.base import Base
from database.models.trading import Position, TradeOrder, TradingAccount
from database.session import create_engine_from_url, get_session
from trading.exceptions import VirtualTradingConfigError


def setup_session_with_order_and_position():
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = get_session(engine)
    account = TradingAccount(
        name="AI Simulation",
        type="AI_SIMULATION",
        cash=Decimal("1000000"),
        total_asset=Decimal("1000000"),
        initial_cash=Decimal("1000000"),
    )
    session.add(account)
    session.flush()
    session.add(
        TradeOrder(
            account_id=account.id,
            stock_code="000001",
            action="BUY",
            order_price=Decimal("7.69"),
            order_quantity=100,
            filled_quantity=0,
            status="PENDING",
            submit_time=datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc),
        )
    )
    session.add(
        Position(
            account_id=account.id,
            stock_code="000001",
            quantity=100,
            available_quantity=0,
            cost_price=Decimal("8.00"),
            buy_date=date(2026, 1, 5),
        )
    )
    session.commit()
    return engine, session


def test_scan_virtual_orders_generates_alert_event() -> None:
    engine, session = setup_session_with_order_and_position()
    try:
        alerts = IntradayAlertService(session=session).scan_virtual_orders()

        assert alerts
        assert alerts[0].alert_type == "PENDING_ORDER_NEAR_FILL"
        assert alerts[0].related_virtual_order_id is not None
    finally:
        session.close()
        engine.dispose()


def test_intraday_scan_is_advisory_only_and_does_not_modify_virtual_order() -> None:
    engine, session = setup_session_with_order_and_position()
    try:
        alerts = IntradayAlertService(session=session).run_intraday_scan()
        order = session.scalars(select(TradeOrder)).first()

        assert alerts
        assert order.status == "PENDING"
    finally:
        session.close()
        engine.dispose()


def test_scan_virtual_positions_runs_without_real_sources() -> None:
    engine, session = setup_session_with_order_and_position()
    try:
        alerts = IntradayAlertService(session=session).scan_virtual_positions()

        assert isinstance(alerts, list)
    finally:
        session.close()
        engine.dispose()


def test_real_order_update_enabled_is_rejected() -> None:
    raw = load_alert_rules_config().raw | {
        "recheck": {
            "mode": "advisory_only",
            "allow_virtual_order_update": False,
            "real_order_update_enabled": True,
        }
    }
    with pytest.raises(Exception):
        AlertRulesConfig(raw=raw).validate()
