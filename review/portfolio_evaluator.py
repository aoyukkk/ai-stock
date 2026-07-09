from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from database.models.trading import Position, TradeOrder, TradingAccount
from database.session import get_session, init_db
from datasource.service import DataSourceService
from trading.config import VirtualTradingConfig, load_virtual_trading_config
from review.schemas import PortfolioEvaluationResult


FILLED_STATUSES = {"FILLED", "PARTIAL_FILLED"}


class PortfolioEvaluator:
    def __init__(
        self,
        session=None,
        data_source_service: DataSourceService | None = None,
        virtual_config: VirtualTradingConfig | None = None,
    ) -> None:
        self.session = session
        self.data_source_service = data_source_service or DataSourceService()
        self.virtual_config = virtual_config or load_virtual_trading_config()
        self.virtual_config.validate()

    def evaluate_account(self, account_id: int, evaluation_date: date) -> PortfolioEvaluationResult:
        session, own_session = self._session()
        try:
            account = session.get(TradingAccount, account_id)
            if account is None:
                return empty_portfolio_result(account_id=account_id, account_type="UNKNOWN")

            positions = session.scalars(
                select(Position).where(Position.account_id == account.id).order_by(Position.stock_code)
            ).all()
            latest_prices = self._latest_prices([position.stock_code for position in positions])
            market_value = Decimal("0")
            wins = 0
            win_total = 0
            for position in positions:
                latest_price = latest_prices.get(position.stock_code, position.cost_price or Decimal("0"))
                market_value += latest_price * Decimal(position.quantity)
                if position.quantity > 0 and position.cost_price:
                    win_total += 1
                    if latest_price > position.cost_price:
                        wins += 1

            cash = account.cash or Decimal("0")
            total_asset = _money(cash + market_value)
            initial_cash = account.initial_cash or total_asset
            profit_loss = _money(total_asset - initial_cash)
            profit_loss_percent = _pct(profit_loss / initial_cash) if initial_cash > 0 else Decimal("0")
            max_drawdown = abs(profit_loss_percent) if profit_loss_percent < 0 else Decimal("0")

            orders = [
                order
                for order in session.scalars(
                    select(TradeOrder)
                    .where(TradeOrder.account_id == account.id)
                    .order_by(TradeOrder.submit_time, TradeOrder.id)
                ).all()
                if order.submit_time.date() <= evaluation_date
            ]
            filled_orders = [
                order
                for order in orders
                if order.status in FILLED_STATUSES and order.filled_quantity > 0
            ]
            win_rate = _pct(Decimal(wins) / Decimal(win_total)) if win_total else Decimal("0")
            fill_rate = _pct(Decimal(len(filled_orders)) / Decimal(len(orders))) if orders else Decimal("0")

            return PortfolioEvaluationResult(
                account_id=account.id,
                account_type=account.type,
                initial_cash=initial_cash,
                cash=cash,
                total_asset=total_asset,
                market_value=_money(market_value),
                profit_loss=profit_loss,
                profit_loss_percent=profit_loss_percent,
                max_drawdown=max_drawdown.quantize(Decimal("0.0001")),
                win_rate=win_rate,
                order_count=len(orders),
                filled_order_count=len(filled_orders),
                order_fill_rate=fill_rate,
            )
        finally:
            if own_session:
                session.close()

    def evaluate_ai_simulation(self, evaluation_date: date) -> PortfolioEvaluationResult:
        session, own_session = self._session()
        try:
            account = session.scalar(
                select(TradingAccount)
                .where(TradingAccount.type == self.virtual_config.account_type)
                .order_by(TradingAccount.id)
            )
            if account is None:
                account = TradingAccount(
                    name="AI Simulation",
                    type=self.virtual_config.account_type,
                    cash=self.virtual_config.initial_cash,
                    total_asset=self.virtual_config.initial_cash,
                    initial_cash=self.virtual_config.initial_cash,
                )
                session.add(account)
                session.commit()
                session.refresh(account)
            return self.evaluate_account(account.id, evaluation_date)
        finally:
            if own_session:
                session.close()

    def _latest_prices(self, stock_codes: list[str]) -> dict[str, Decimal]:
        if not stock_codes:
            return {}
        return {
            quote.stock_code: quote.current_price
            for quote in self.data_source_service.get_realtime_quotes(stock_codes)
        }

    def _session(self):
        if self.session is not None:
            return self.session, False
        init_db()
        return get_session(), True


def empty_portfolio_result(account_id: int | None, account_type: str) -> PortfolioEvaluationResult:
    return PortfolioEvaluationResult(
        account_id=account_id,
        account_type=account_type,
        initial_cash=Decimal("0"),
        cash=Decimal("0"),
        total_asset=Decimal("0"),
        market_value=Decimal("0"),
        profit_loss=Decimal("0"),
        profit_loss_percent=Decimal("0"),
        max_drawdown=Decimal("0"),
        win_rate=Decimal("0"),
        order_count=0,
        filled_order_count=0,
        order_fill_rate=Decimal("0"),
    )


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"))


def _pct(value: Decimal) -> Decimal:
    return (value * Decimal("100")).quantize(Decimal("0.0001"))
