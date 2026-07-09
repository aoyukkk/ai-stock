from __future__ import annotations

from datetime import date
from decimal import Decimal

import database.models  # noqa: F401
from database.base import Base
from database.models.trading import TradingAccount
from database.session import create_engine_from_url, get_session
from review.comparison import AccountComparison


def test_comparison_handles_missing_human_account() -> None:
    result = AccountComparison().compare_ai_vs_human(None, None, date(2026, 1, 5))

    assert result.human_summary == "No human trading record available."
    assert result.ai_better_on_profit is None


def test_comparison_compares_basic_metrics_when_human_account_exists() -> None:
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = get_session(engine)

    try:
        ai_account = TradingAccount(
            name="AI Simulation",
            type="AI_SIMULATION",
            cash=Decimal("10500.00"),
            total_asset=Decimal("10500.00"),
            initial_cash=Decimal("10000.00"),
        )
        human_account = TradingAccount(
            name="Manual",
            type="HUMAN",
            cash=Decimal("10100.00"),
            total_asset=Decimal("10100.00"),
            initial_cash=Decimal("10000.00"),
        )
        session.add_all([ai_account, human_account])
        session.commit()

        result = AccountComparison(session=session).compare_ai_vs_human(
            ai_account.id,
            human_account.id,
            date(2026, 1, 5),
        )

        assert result.human_account_id == human_account.id
        assert result.ai_better_on_profit is True
        assert result.ai_profit_loss_percent == Decimal("5.0000")
        assert result.human_profit_loss_percent == Decimal("1.0000")
    finally:
        session.close()
        engine.dispose()
