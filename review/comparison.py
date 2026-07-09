from __future__ import annotations

from datetime import date

from sqlalchemy import select

from database.models.trading import TradingAccount
from database.session import get_session, init_db
from review.portfolio_evaluator import PortfolioEvaluator
from review.schemas import ComparisonResult


class AccountComparison:
    def __init__(self, session=None, portfolio_evaluator: PortfolioEvaluator | None = None) -> None:
        self.session = session
        self.portfolio_evaluator = portfolio_evaluator

    def compare_ai_vs_human(
        self,
        ai_account_id: int | None,
        human_account_id: int | None,
        evaluation_date: date,
    ) -> ComparisonResult:
        session, own_session = self._session()
        try:
            if human_account_id is None:
                return ComparisonResult(
                    ai_account_id=ai_account_id,
                    human_account_id=None,
                    human_summary="No human trading record available.",
                )

            human_account = session.get(TradingAccount, human_account_id)
            if human_account is None:
                return ComparisonResult(
                    ai_account_id=ai_account_id,
                    human_account_id=human_account_id,
                    human_summary="No human trading record available.",
                )

            evaluator = self.portfolio_evaluator or PortfolioEvaluator(session=session)
            ai_result = (
                evaluator.evaluate_account(ai_account_id, evaluation_date)
                if ai_account_id is not None
                else evaluator.evaluate_ai_simulation(evaluation_date)
            )
            human_result = evaluator.evaluate_account(human_account.id, evaluation_date)
            ai_better = ai_result.profit_loss_percent >= human_result.profit_loss_percent

            return ComparisonResult(
                ai_account_id=ai_result.account_id,
                human_account_id=human_result.account_id,
                human_summary=(
                    "Human trading record compared. "
                    f"AI P/L={ai_result.profit_loss_percent}%, "
                    f"human P/L={human_result.profit_loss_percent}%."
                ),
                ai_better_on_profit=ai_better,
                ai_profit_loss_percent=ai_result.profit_loss_percent,
                human_profit_loss_percent=human_result.profit_loss_percent,
                ai_win_rate=ai_result.win_rate,
                human_win_rate=human_result.win_rate,
                ai_max_drawdown=ai_result.max_drawdown,
                human_max_drawdown=human_result.max_drawdown,
                details={
                    "ai_order_fill_rate": float(ai_result.order_fill_rate),
                    "human_order_fill_rate": float(human_result.order_fill_rate),
                    "basic_comparison_only": True,
                },
            )
        finally:
            if own_session:
                session.close()

    def _session(self):
        if self.session is not None:
            return self.session, False
        init_db()
        return get_session(), True


def compare_ai_vs_human(
    ai_account_id: int | None,
    human_account_id: int | None,
    evaluation_date: date,
    session=None,
) -> ComparisonResult:
    return AccountComparison(session=session).compare_ai_vs_human(
        ai_account_id=ai_account_id,
        human_account_id=human_account_id,
        evaluation_date=evaluation_date,
    )


def find_human_account_id(session) -> int | None:
    account = session.scalar(
        select(TradingAccount)
        .where(TradingAccount.type.in_(["HUMAN", "MANUAL", "HUMAN_SIMULATION"]))
        .order_by(TradingAccount.id)
    )
    return account.id if account is not None else None
