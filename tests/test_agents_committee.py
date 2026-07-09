from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

import database.models  # noqa: F401
from agents.committee import AICommitteeService
from database.base import Base
from database.models.ai import AIAnalysisResult, DecisionSnapshot, PredictionRecord, StockAIScore
from database.models.order_plan import OrderPlan
from database.models.trading import TradeOrder, TradeRecord
from database.session import create_engine_from_url, get_session
from llm_gateway.service import LLMGatewayService
from screening.schemas import LightScreeningRanking, LightScreeningResult


class StubLightScreeningService:
    def __init__(self, results: list[LightScreeningResult]) -> None:
        self.results = results

    def run_light_screening(self, top_n=None, persist=False):
        selected = self.results[: int(top_n or len(self.results))]
        return LightScreeningRanking(
            generated_at=datetime.now(timezone.utc),
            quant_universe_size=len(self.results),
            requested_quant_top_q=len(self.results),
            requested_top_n=int(top_n or len(selected)),
            returned_count=len(selected),
            results=selected,
        )


def make_light_result(code: str, score: str, rank: int, risk_note: str = "compressed risk note") -> LightScreeningResult:
    return LightScreeningResult(
        stock_code=code,
        stock_name=f"Stock {code}",
        industry="mock",
        quant_rank=rank,
        quant_total_score=Decimal(score),
        llm_score=Decimal(score),
        final_light_score=Decimal(score),
        rank=rank,
        direction="WATCH",
        confidence=Decimal("0.70"),
        should_keep=True,
        reason="compressed light reason",
        risk_note=risk_note,
    )


def make_service(results: list[LightScreeningResult]) -> AICommitteeService:
    return AICommitteeService(
        light_screening_service=StubLightScreeningService(results),
        llm_service=LLMGatewayService(),
    )


def test_committee_service_runs_and_sorts_by_final_score() -> None:
    service = make_service([
        make_light_result("000001", "70", 1),
        make_light_result("000002", "80", 2),
    ])

    ranking = service.run_committee(input_top_n=2, final_top_n=2, persist=False)

    assert ranking.input_count == 2
    assert ranking.returned_count == 2
    scores = [result.final_score for result in ranking.results]
    assert scores == sorted(scores, reverse=True)
    assert all(len(result.agent_outputs) == 6 for result in ranking.results)


def test_risk_agent_block_forces_blocked_recommendation() -> None:
    service = make_service([
        make_light_result("000001", "88", 1, risk_note="black swan block risk"),
    ])

    ranking = service.run_committee(input_top_n=1, final_top_n=1, persist=False)

    assert ranking.results[0].recommendation == "BLOCKED"
    assert ranking.results[0].risk_level == "BLACK_SWAN"


def test_committee_persist_false_does_not_write_database() -> None:
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = get_session(engine)

    try:
        service = make_service([make_light_result("000001", "70", 1)])
        ranking = service.run_committee(input_top_n=1, final_top_n=1, persist=False, session=session)

        assert ranking.returned_count == 1
        assert session.scalars(select(AIAnalysisResult)).all() == []
        assert session.scalars(select(StockAIScore)).all() == []
        assert session.scalars(select(DecisionSnapshot)).all() == []
        assert session.scalars(select(PredictionRecord)).all() == []
    finally:
        session.close()
        engine.dispose()


def test_committee_persist_true_writes_ai_tables_but_not_order_or_trade_tables() -> None:
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = get_session(engine)

    try:
        service = make_service([make_light_result("000001", "70", 1)])
        ranking = service.run_committee(input_top_n=1, final_top_n=1, persist=True, session=session)

        assert ranking.returned_count == 1
        assert len(session.scalars(select(AIAnalysisResult)).all()) == 7
        assert len(session.scalars(select(StockAIScore)).all()) == 1
        assert len(session.scalars(select(DecisionSnapshot)).all()) == 1
        assert len(session.scalars(select(PredictionRecord)).all()) == 1
        assert session.scalars(select(OrderPlan)).all() == []
        assert session.scalars(select(TradeOrder)).all() == []
        assert session.scalars(select(TradeRecord)).all() == []
    finally:
        session.close()
        engine.dispose()


def test_committee_results_do_not_generate_order_prices() -> None:
    ranking = make_service([make_light_result("000001", "70", 1)]).run_committee(
        input_top_n=1,
        final_top_n=1,
        persist=False,
    )
    text = ranking.model_dump_json().lower()

    assert "recommended_price" not in text
    assert "stop_loss_price" not in text
    assert "take_profit" not in text
    assert "trade_order" not in text
