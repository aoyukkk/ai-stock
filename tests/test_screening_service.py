from sqlalchemy import select

import database.models  # noqa: F401
from database.base import Base
from database.models.ai import AIAnalysisResult, DecisionSnapshot, PredictionRecord, StockAIScore
from database.session import create_engine_from_url, get_session
from llm_gateway.service import LLMGatewayService
from screening.service import LightScreeningService


def test_light_screening_service_runs_quant_to_mock_llm_flow() -> None:
    service = LightScreeningService(llm_service=LLMGatewayService())
    ranking = service.run_light_screening(quant_top_q=8, top_n=5, persist=False)

    assert ranking.quant_universe_size >= 8
    assert ranking.requested_quant_top_q == 8
    assert ranking.requested_top_n == 5
    assert ranking.returned_count <= 5
    assert ranking.results
    scores = [item.final_light_score for item in ranking.results]
    assert scores == sorted(scores, reverse=True)
    assert all(item.model_name for item in ranking.results)
    assert all(item.request_hash for item in ranking.results)


def test_light_screening_uses_mock_llm_and_no_real_sources() -> None:
    llm_service = LLMGatewayService()
    service = LightScreeningService(llm_service=llm_service)
    ranking = service.run_light_screening(quant_top_q=5, top_n=3, persist=False)

    assert ranking.results
    assert llm_service.get_usage_summary()["call_count"] >= 1
    assert all("Mock light screening" in item.reason for item in ranking.results)


def test_light_screening_persist_true_writes_ai_tables() -> None:
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = get_session(engine)

    try:
        ranking = LightScreeningService(llm_service=LLMGatewayService()).run_light_screening(
            quant_top_q=5,
            top_n=3,
            persist=True,
            session=session,
        )

        assert len(session.scalars(select(AIAnalysisResult)).all()) == ranking.returned_count
        assert len(session.scalars(select(StockAIScore)).all()) == ranking.returned_count
        assert len(session.scalars(select(DecisionSnapshot)).all()) == ranking.returned_count
        assert len(session.scalars(select(PredictionRecord)).all()) == ranking.returned_count
    finally:
        session.close()
        engine.dispose()
