from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

import database.models  # noqa: F401
from agents.schemas import CommitteeRanking, CommitteeStockResult
from database.base import Base
from database.models.order_plan import OrderPlan, OrderPriceCandidate
from database.models.trading import TradeOrder, TradeRecord
from database.session import create_engine_from_url, get_session
from datasource.service import DataSourceService
from order_price.service import OrderPriceService


class StubCommitteeService:
    def run_committee(self, input_top_n=None, final_top_n=None, persist=False):
        results = [
            CommitteeStockResult(
                stock_code="600276",
                stock_name="Mock Stock",
                industry="mock",
                quant_score=Decimal("80"),
                light_score=Decimal("78"),
                technical_score=Decimal("82"),
                news_score=Decimal("80"),
                capital_score=Decimal("79"),
                emotion_score=Decimal("76"),
                overseas_score=Decimal("70"),
                risk_score=Decimal("85"),
                final_score=Decimal("80"),
                recommendation="WATCH",
                risk_level="LOW",
                confidence=Decimal("0.80"),
                controller_reason="stub",
                risk_note="none",
                agent_outputs=[],
                rank=1,
            )
        ]
        return CommitteeRanking(
            generated_at=datetime.now(timezone.utc),
            input_count=1,
            requested_top_n=1,
            returned_count=1,
            results=results,
        )


def make_service() -> OrderPriceService:
    return OrderPriceService(
        committee_service=StubCommitteeService(),
        data_source_service=DataSourceService(),
    )


def test_order_price_service_runs_committee_to_plan_flow() -> None:
    ranking = make_service().generate_order_plans(input_top_n=1, persist=False)

    assert ranking.input_count == 1
    assert ranking.returned_count == 1
    plan = ranking.plans[0]
    assert plan.recommended_price is not None
    assert plan.recommended_price <= plan.max_acceptable_price
    assert len(plan.candidates) == 3


def test_order_price_persist_false_does_not_write_database() -> None:
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = get_session(engine)

    try:
        ranking = make_service().generate_order_plans(input_top_n=1, persist=False, session=session)

        assert ranking.returned_count == 1
        assert session.scalars(select(OrderPlan)).all() == []
        assert session.scalars(select(OrderPriceCandidate)).all() == []
    finally:
        session.close()
        engine.dispose()


def test_order_price_persist_true_writes_plan_and_candidates_only() -> None:
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = get_session(engine)

    try:
        ranking = make_service().generate_order_plans(input_top_n=1, persist=True, session=session)

        assert ranking.returned_count == 1
        plans = session.scalars(select(OrderPlan)).all()
        candidates = session.scalars(select(OrderPriceCandidate)).all()
        assert len(plans) == 1
        assert len(candidates) == 3
        assert session.scalars(select(TradeOrder)).all() == []
        assert session.scalars(select(TradeRecord)).all() == []
    finally:
        session.close()
        engine.dispose()
