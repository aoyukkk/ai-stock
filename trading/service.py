from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from database.models.order_plan import OrderPlan
from database.session import get_session, init_db
from order_price.service import OrderPriceService
from trading.config import VirtualTradingConfig, load_virtual_trading_config
from trading.exceptions import VirtualBrokerError
from trading.schemas import VirtualExecutionReport
from trading.virtual_broker import VirtualBroker


class VirtualTradingService:
    def __init__(
        self,
        session=None,
        order_price_service: OrderPriceService | None = None,
        config: VirtualTradingConfig | None = None,
    ) -> None:
        self.session = session
        self.config = config or load_virtual_trading_config()
        self.order_price_service = order_price_service or OrderPriceService()
        self.config.validate()

    def create_default_ai_account(self):
        session, own_session = self._session()
        try:
            return VirtualBroker(session=session, config=self.config).create_account(
                name="AI Simulation",
                initial_cash=self.config.initial_cash,
            )
        finally:
            if own_session:
                session.close()

    def get_account_summary(self, account_id: int | None = None):
        session, own_session = self._session()
        try:
            broker = VirtualBroker(session=session, config=self.config)
            account = broker.create_account() if account_id is None else broker.get_account(account_id)
            return account
        finally:
            if own_session:
                session.close()

    def submit_order_from_plan(self, order_plan_id: int, account_id: int | None = None):
        session, own_session = self._session()
        try:
            broker = VirtualBroker(session=session, config=self.config)
            account = broker.create_account() if account_id is None else broker.get_account(account_id)
            plan = session.get(OrderPlan, order_plan_id)
            if plan is None:
                raise VirtualBrokerError("Order plan not found.")
            if plan.status != "DRAFT" or plan.side != "BUY" or plan.recommended_price is None:
                raise VirtualBrokerError("Only DRAFT BUY order plans can be submitted to virtual trading.")
            quantity = self._calculate_order_quantity(account.cash, plan.recommended_price)
            return broker.buy(
                account_id=account.account_id,
                stock_code=plan.stock_code,
                price=plan.recommended_price,
                quantity=quantity,
                order_plan_id=plan.id,
                reason="submitted from order plan",
            )
        finally:
            if own_session:
                session.close()

    def run_order_plans(
        self,
        input_top_n: int | None = None,
        account_id: int | None = None,
        persist_plans: bool = False,
    ) -> VirtualExecutionReport:
        session, own_session = self._session()
        try:
            broker = VirtualBroker(session=session, config=self.config)
            account = broker.create_account() if account_id is None else broker.get_account(account_id)
            ranking = self.order_price_service.generate_order_plans(
                input_top_n=input_top_n,
                persist=persist_plans,
                session=session if persist_plans else None,
            )
            submitted_orders = []
            for plan in ranking.plans:
                if plan.status != "DRAFT" or plan.side != "BUY" or plan.recommended_price is None:
                    continue
                latest_account = broker.get_account(account.account_id)
                quantity = self._calculate_order_quantity(latest_account.cash, plan.recommended_price)
                if quantity <= 0:
                    continue
                submitted_orders.append(
                    broker.buy(
                        account_id=account.account_id,
                        stock_code=plan.stock_code,
                        price=plan.recommended_price,
                        quantity=quantity,
                        order_plan_id=None,
                        reason="run_order_plans",
                    )
                )

            account_snapshot = broker.get_account(account.account_id)
            return VirtualExecutionReport(
                generated_at=datetime.now(timezone.utc),
                account=account_snapshot,
                orders=submitted_orders,
                positions=broker.get_positions(account.account_id),
                trades=broker.get_trades(account.account_id),
                summary={
                    "virtual_only": True,
                    "real_trading_enabled": False,
                    "input_top_n": input_top_n,
                    "generated_plans": ranking.returned_count,
                    "submitted_orders": len(submitted_orders),
                    "filled_orders": len([order for order in submitted_orders if order.status in {"FILLED", "PARTIAL_FILLED"}]),
                },
            )
        finally:
            if own_session:
                session.close()

    def cancel_order(self, order_id: int, reason: str | None = None):
        session, own_session = self._session()
        try:
            return VirtualBroker(session=session, config=self.config).cancel_order(order_id, reason=reason)
        finally:
            if own_session:
                session.close()

    def reprice_order(self, order_id: int, new_price: Decimal, reason: str | None = None):
        session, own_session = self._session()
        try:
            return VirtualBroker(session=session, config=self.config).reprice_order(order_id, new_price, reason=reason)
        finally:
            if own_session:
                session.close()

    def get_positions(self, account_id: int | None = None):
        session, own_session = self._session()
        try:
            broker = VirtualBroker(session=session, config=self.config)
            account = broker.create_account() if account_id is None else broker.get_account(account_id)
            return broker.get_positions(account.account_id)
        finally:
            if own_session:
                session.close()

    def get_orders(self, account_id: int | None = None):
        session, own_session = self._session()
        try:
            broker = VirtualBroker(session=session, config=self.config)
            account = broker.create_account() if account_id is None else broker.get_account(account_id)
            return broker.get_orders(account.account_id)
        finally:
            if own_session:
                session.close()

    def get_trades(self, account_id: int | None = None):
        session, own_session = self._session()
        try:
            broker = VirtualBroker(session=session, config=self.config)
            account = broker.create_account() if account_id is None else broker.get_account(account_id)
            return broker.get_trades(account.account_id)
        finally:
            if own_session:
                session.close()

    def config_summary(self) -> dict:
        return self.config.summary()

    def _calculate_order_quantity(self, cash: Decimal, price: Decimal) -> int:
        lot_size = int(self.config.rules.get("lot_size", 100))
        if price <= 0:
            return 0
        budget = cash * Decimal("0.10")
        quantity = int((budget / price) // Decimal(lot_size)) * lot_size
        return max(quantity, 0)

    def _session(self):
        if self.session is not None:
            return self.session, False
        init_db()
        return get_session(), True
