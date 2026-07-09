from datetime import date
from decimal import Decimal

import database.models  # noqa: F401
from database.base import Base
from database.models.order_plan import OrderPlan
from database.session import create_engine_from_url, get_session
from recheck.service import RecheckService


def test_recheck_service_runs_pre_market_and_intraday_scan() -> None:
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = get_session(engine)
    try:
        session.add(
            OrderPlan(
                stock_code="000001",
                plan_date=date(2026, 1, 5),
                plan_session="POST_MARKET",
                side="BUY",
                strategy_type="BALANCED",
                recommended_price=Decimal("8.00"),
                status="DRAFT",
            )
        )
        session.commit()

        service = RecheckService(session=session)
        pre_market = service.run_pre_market(limit=1)
        intraday = service.run_intraday_scan()

        assert len(pre_market) == 1
        assert isinstance(intraday, list)
        assert service.config_summary()["advisory_only"] is True
    finally:
        session.close()
        engine.dispose()
