from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from database.models.trading import Position, TradeOrder, TradeRecord, TradingAccount
from datasource.service import DataSourceService
from trading.config import VirtualTradingConfig, load_virtual_trading_config
from trading.exceptions import TradingRuleError, VirtualBrokerError
from trading.execution_simulator import simulate_order_fill
from trading.fees import calculate_total_cost
from trading.persistence import quantize_money, virtual_reason
from trading.rules import (
    validate_cash_enough,
    validate_lot_size,
    validate_not_suspended,
    validate_price_limit,
    validate_t_plus_one,
)
from trading.schemas import (
    VirtualAccountSnapshot,
    VirtualOrderResult,
    VirtualPositionSnapshot,
    VirtualTradeResult,
)


class VirtualBroker:
    def __init__(
        self,
        session,
        config: VirtualTradingConfig | None = None,
        data_source_service: DataSourceService | None = None,
    ) -> None:
        self.session = session
        self.config = config or load_virtual_trading_config()
        self.data_source_service = data_source_service or DataSourceService()
        self.config.validate()

    def create_account(self, name: str = "AI Simulation", initial_cash: Decimal | None = None) -> VirtualAccountSnapshot:
        existing = self.session.scalar(
            select(TradingAccount).where(
                TradingAccount.name == name,
                TradingAccount.type == self.config.account_type,
            )
        )
        if existing is None:
            cash = quantize_money(initial_cash or self.config.initial_cash)
            existing = TradingAccount(
                name=name,
                type=self.config.account_type,
                cash=cash,
                total_asset=cash,
                initial_cash=cash,
            )
            self.session.add(existing)
            self.session.commit()
        return self.get_account(existing.id)

    def get_account(self, account_id: int) -> VirtualAccountSnapshot:
        account = self._account(account_id)
        return self.update_account_asset(account.id)

    def get_cash(self, account_id: int) -> Decimal:
        return self._account(account_id).cash or Decimal("0")

    def get_positions(self, account_id: int) -> list[VirtualPositionSnapshot]:
        positions = self.session.scalars(
            select(Position).where(Position.account_id == account_id).order_by(Position.stock_code)
        ).all()
        prices = self._latest_prices([position.stock_code for position in positions])
        snapshots: list[VirtualPositionSnapshot] = []
        for position in positions:
            latest_price = prices.get(position.stock_code, position.cost_price or Decimal("0"))
            market_value = quantize_money(latest_price * Decimal(position.quantity))
            cost_price = position.cost_price or Decimal("0")
            unrealized = quantize_money((latest_price - cost_price) * Decimal(position.quantity))
            snapshots.append(
                VirtualPositionSnapshot(
                    account_id=position.account_id,
                    stock_code=position.stock_code,
                    quantity=position.quantity,
                    available_quantity=position.available_quantity,
                    cost_price=position.cost_price,
                    latest_price=latest_price,
                    market_value=market_value,
                    unrealized_pnl=unrealized,
                    buy_date=position.buy_date,
                )
            )
        return snapshots

    def get_orders(self, account_id: int) -> list[VirtualOrderResult]:
        orders = self.session.scalars(
            select(TradeOrder).where(TradeOrder.account_id == account_id).order_by(TradeOrder.submit_time, TradeOrder.id)
        ).all()
        return [self._order_result(order) for order in orders]

    def get_trades(self, account_id: int) -> list[VirtualTradeResult]:
        trades = self.session.scalars(
            select(TradeRecord).where(TradeRecord.account_id == account_id).order_by(TradeRecord.time, TradeRecord.id)
        ).all()
        return [self._trade_result(trade) for trade in trades]

    def buy(
        self,
        account_id: int,
        stock_code: str,
        price: Decimal,
        quantity: int,
        order_plan_id: int | None = None,
        reason: str | None = None,
    ) -> VirtualOrderResult:
        return self._submit_order(account_id, stock_code, "BUY", price, quantity, order_plan_id, reason)

    def sell(
        self,
        account_id: int,
        stock_code: str,
        price: Decimal,
        quantity: int,
        order_plan_id: int | None = None,
        reason: str | None = None,
    ) -> VirtualOrderResult:
        return self._submit_order(account_id, stock_code, "SELL", price, quantity, order_plan_id, reason)

    def cancel_order(self, order_id: int, reason: str | None = None) -> VirtualOrderResult:
        if not bool(self.config.rules.get("support_cancel_order", True)):
            raise VirtualBrokerError("Virtual cancel order is disabled by config.")
        order = self._order(order_id)
        if order.status != "PENDING":
            return self._order_result(order)
        order.status = "CANCELLED"
        order.fail_reason = virtual_reason(reason or "cancelled")
        self.session.commit()
        return self._order_result(order)

    def reprice_order(self, order_id: int, new_price: Decimal, reason: str | None = None) -> VirtualOrderResult:
        if not bool(self.config.rules.get("support_reprice_order", True)):
            raise VirtualBrokerError("Virtual reprice order is disabled by config.")
        order = self._order(order_id)
        if order.status != "PENDING":
            return self._order_result(order)
        order.order_price = new_price
        order.status = "REPRICED"
        order.fail_reason = virtual_reason(reason or "repriced")
        self.session.commit()
        return self._order_result(order)

    def settle_t_plus_one(self, account_id: int, trade_date: date) -> None:
        positions = self.session.scalars(select(Position).where(Position.account_id == account_id)).all()
        for position in positions:
            if position.buy_date is None or position.buy_date < trade_date:
                position.available_quantity = position.quantity
        self.session.commit()

    def update_account_asset(self, account_id: int) -> VirtualAccountSnapshot:
        account = self._account(account_id)
        positions = self.session.scalars(select(Position).where(Position.account_id == account_id)).all()
        prices = self._latest_prices([position.stock_code for position in positions])
        market_value = Decimal("0")
        for position in positions:
            price = prices.get(position.stock_code, position.cost_price or Decimal("0"))
            market_value += price * Decimal(position.quantity)
        cash = account.cash or Decimal("0")
        total_asset = quantize_money(cash + market_value)
        account.total_asset = total_asset
        self.session.commit()
        initial_cash = account.initial_cash or Decimal("0")
        profit_loss = quantize_money(total_asset - initial_cash)
        profit_loss_percent = Decimal("0.0000")
        if initial_cash > 0:
            profit_loss_percent = quantize_money(profit_loss / initial_cash * Decimal("100"))
        return VirtualAccountSnapshot(
            account_id=account.id,
            name=account.name,
            cash=cash,
            total_asset=total_asset,
            initial_cash=initial_cash,
            market_value=quantize_money(market_value),
            profit_loss=profit_loss,
            profit_loss_percent=profit_loss_percent,
            updated_at=datetime.now(timezone.utc),
        )

    def _submit_order(
        self,
        account_id: int,
        stock_code: str,
        action: str,
        price: Decimal,
        quantity: int,
        order_plan_id: int | None,
        reason: str | None,
    ) -> VirtualOrderResult:
        account = self._account(account_id)
        now = datetime.now(timezone.utc)
        order = TradeOrder(
            account_id=account_id,
            stock_code=stock_code,
            action=action,
            order_price=price,
            order_quantity=quantity,
            filled_quantity=0,
            status="PENDING",
            submit_time=now,
            order_plan_id=order_plan_id,
        )
        self.session.add(order)
        self.session.flush()

        try:
            quote, kline_bar, limit_price, stock_status = self._market_context(stock_code)
            trade_date = quote.quote_time.date()
            validate_not_suspended(stock_status, bool(self.config.rules.get("allow_trade_when_suspended", False)))
            validate_lot_size(quantity, int(self.config.rules.get("lot_size", 100)))
            if bool(self.config.rules.get("price_limit", True)):
                validate_price_limit(action, price, limit_price.limit_up_price, limit_price.limit_down_price)
            position = self._position(account_id, stock_code)
            if action == "SELL" and bool(self.config.rules.get("t_plus_one", True)):
                validate_t_plus_one(action, position, quantity, trade_date)
            if action == "BUY":
                total_cost = calculate_total_cost(action, price, quantity, self.config)
                validate_cash_enough(account.cash or Decimal("0"), total_cost["cash_delta"])
        except TradingRuleError as exc:
            order.status = "FAILED"
            order.fail_reason = virtual_reason(str(exc))
            self.session.commit()
            return self._order_result(order)

        fill = simulate_order_fill(
            action=action,
            order_price=price,
            order_quantity=quantity,
            market_snapshot=quote,
            kline_bar=kline_bar,
            limit_price=limit_price,
            config=self.config,
        )
        order.status = fill.status
        order.filled_quantity = fill.filled_quantity
        order.fail_reason = virtual_reason(fill.fail_reason) if fill.fail_reason else None
        if fill.filled_quantity:
            order.filled_time = now
            self._apply_fill(account, order, fill.filled_quantity, quote.quote_time.date(), reason)
        self.session.commit()
        return self._order_result(order)

    def _apply_fill(
        self,
        account: TradingAccount,
        order: TradeOrder,
        filled_quantity: int,
        trade_date: date,
        reason: str | None,
    ) -> None:
        cost = calculate_total_cost(order.action, order.order_price or Decimal("0"), filled_quantity, self.config)
        if order.action == "BUY":
            account.cash = quantize_money((account.cash or Decimal("0")) - cost["cash_delta"])
            self._increase_position(
                account.id,
                order.stock_code,
                filled_quantity,
                cost["price"],
                trade_date,
            )
        else:
            account.cash = quantize_money((account.cash or Decimal("0")) + cost["cash_delta"])
            self._decrease_position(account.id, order.stock_code, filled_quantity)

        self.session.add(
            TradeRecord(
                account_id=account.id,
                stock_code=order.stock_code,
                action=order.action,
                price=cost["price"],
                quantity=filled_quantity,
                amount=cost["amount"],
                commission=cost["commission"],
                stamp_tax=cost["stamp_tax"],
                slippage=cost["slippage"],
                order_status=order.status,
                time=order.filled_time or datetime.now(timezone.utc),
                reason=virtual_reason(reason),
                order_plan_id=order.order_plan_id,
            )
        )

    def _increase_position(self, account_id: int, stock_code: str, quantity: int, price: Decimal, buy_date: date) -> None:
        position = self._position(account_id, stock_code)
        if position is None:
            position = Position(
                account_id=account_id,
                stock_code=stock_code,
                quantity=0,
                available_quantity=0,
                cost_price=price,
                buy_date=buy_date,
            )
            self.session.add(position)
            self.session.flush()
        old_quantity = position.quantity
        old_cost = position.cost_price or price
        new_quantity = old_quantity + quantity
        position.cost_price = quantize_money(
            (old_cost * Decimal(old_quantity) + price * Decimal(quantity)) / Decimal(new_quantity)
        )
        position.quantity = new_quantity
        if not bool(self.config.rules.get("t_plus_one", True)):
            position.available_quantity += quantity
        position.buy_date = buy_date

    def _decrease_position(self, account_id: int, stock_code: str, quantity: int) -> None:
        position = self._position(account_id, stock_code)
        if position is None or position.available_quantity < quantity:
            raise TradingRuleError("SELL quantity cannot exceed available virtual position.")
        position.quantity -= quantity
        position.available_quantity -= quantity

    def _market_context(self, stock_code: str):
        quote = self.data_source_service.get_realtime_quotes([stock_code])[0]
        trade_date = quote.quote_time.date()
        kline_bar = self.data_source_service.get_kline(stock_code, trade_date, trade_date, "1d")[-1]
        limit_price = self.data_source_service.get_limit_price(stock_code, trade_date)
        stock_status = "NORMAL"
        for stock in self.data_source_service.get_stock_universe():
            if stock.stock_code == stock_code:
                stock_status = stock.status
                break
        return quote, kline_bar, limit_price, stock_status

    def _latest_prices(self, stock_codes: list[str]) -> dict[str, Decimal]:
        if not stock_codes:
            return {}
        return {
            quote.stock_code: quote.current_price
            for quote in self.data_source_service.get_realtime_quotes(stock_codes)
        }

    def _account(self, account_id: int) -> TradingAccount:
        account = self.session.get(TradingAccount, account_id)
        if account is None or account.type != self.config.account_type:
            raise VirtualBrokerError("AI simulation account not found.")
        return account

    def _order(self, order_id: int) -> TradeOrder:
        order = self.session.get(TradeOrder, order_id)
        if order is None:
            raise VirtualBrokerError("Virtual order not found.")
        return order

    def _position(self, account_id: int, stock_code: str) -> Position | None:
        return self.session.scalar(
            select(Position).where(
                Position.account_id == account_id,
                Position.stock_code == stock_code,
            )
        )

    def _order_result(self, order: TradeOrder) -> VirtualOrderResult:
        return VirtualOrderResult(
            order_id=order.id,
            account_id=order.account_id,
            stock_code=order.stock_code,
            action=order.action,  # type: ignore[arg-type]
            order_price=order.order_price or Decimal("0"),
            order_quantity=order.order_quantity,
            filled_quantity=order.filled_quantity,
            status=order.status,  # type: ignore[arg-type]
            fail_reason=order.fail_reason,
            submit_time=order.submit_time,
            filled_time=order.filled_time,
        )

    def _trade_result(self, trade: TradeRecord) -> VirtualTradeResult:
        return VirtualTradeResult(
            trade_id=trade.id,
            account_id=trade.account_id,
            stock_code=trade.stock_code,
            action=trade.action,  # type: ignore[arg-type]
            price=trade.price or Decimal("0"),
            quantity=trade.quantity,
            amount=trade.amount or Decimal("0"),
            commission=trade.commission or Decimal("0"),
            stamp_tax=trade.stamp_tax or Decimal("0"),
            slippage=trade.slippage or Decimal("0"),
            order_status=trade.order_status or "",
            time=trade.time,
            reason=trade.reason,
        )
