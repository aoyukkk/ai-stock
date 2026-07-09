from __future__ import annotations

from datetime import timedelta

from database.models.ai import AIAnalysisResult, DecisionSnapshot, PredictionRecord, StockAIScore
from agents.exceptions import CommitteePersistenceError
from agents.schemas import CommitteeRanking, CommitteeStockResult


def persist_committee_results(session, ranking: CommitteeRanking) -> None:
    try:
        for result in ranking.results:
            snapshot = DecisionSnapshot(
                stock_code=result.stock_code,
                snapshot_time=ranking.generated_at,
                market_data_json={},
                factor_json={
                    "quant_score": str(result.quant_score),
                    "light_score": str(result.light_score),
                    "technical_score": str(result.technical_score),
                    "news_score": str(result.news_score),
                    "capital_score": str(result.capital_score),
                    "emotion_score": str(result.emotion_score),
                    "overseas_score": str(result.overseas_score),
                    "risk_score": str(result.risk_score),
                },
                news_json={},
                agent_result_json={
                    "agent_outputs": [
                        output.model_dump(mode="json")
                        for output in result.agent_outputs
                    ],
                    "controller": {
                        "final_score": str(result.final_score),
                        "recommendation": result.recommendation,
                        "risk_level": result.risk_level,
                        "controller_reason": result.controller_reason,
                    },
                },
                memory_json={},
                order_price_json={},
                final_score=result.final_score,
                risk_level=result.risk_level,
                recommendation=result.recommendation,
            )
            session.add(snapshot)
            session.flush()

            for output in result.agent_outputs:
                session.add(
                    AIAnalysisResult(
                        stock_code=result.stock_code,
                        agent_name=output.agent_name,
                        model_name=output.model_version,
                        score=output.score,
                        direction=output.direction,
                        confidence=output.confidence,
                        reason=output.reason,
                        risk_note=output.risk_note,
                        analysis_time=ranking.generated_at,
                        prompt_version=output.prompt_version,
                        model_version=output.model_version,
                    )
                )
            session.add(
                AIAnalysisResult(
                    stock_code=result.stock_code,
                    agent_name="controller_agent",
                    model_name=result.model_version,
                    score=result.final_score,
                    direction=_direction_from_recommendation(result),
                    confidence=result.confidence,
                    reason=result.controller_reason,
                    risk_note=result.risk_note,
                    analysis_time=ranking.generated_at,
                    prompt_version=result.prompt_version,
                    model_version=result.model_version,
                )
            )
            session.add(
                StockAIScore(
                    stock_code=result.stock_code,
                    time=ranking.generated_at,
                    technical_score=result.technical_score,
                    news_score=result.news_score,
                    capital_score=result.capital_score,
                    emotion_score=result.emotion_score,
                    overseas_score=result.overseas_score,
                    risk_score=result.risk_score,
                    final_score=result.final_score,
                    recommendation=result.recommendation,
                    risk_level=result.risk_level,
                    confidence=result.confidence,
                    controller_reason=result.controller_reason,
                    decision_snapshot_id=snapshot.id,
                )
            )
            session.add(
                PredictionRecord(
                    stock_code=result.stock_code,
                    prediction_time=ranking.generated_at,
                    prediction_horizon_days=1,
                    expected_direction=_expected_direction(result),
                    score=result.final_score,
                    confidence=result.confidence,
                    recommendation=result.recommendation,
                    reason=result.controller_reason,
                    valid_until=ranking.generated_at + timedelta(days=1),
                    model_version=result.model_version,
                    prompt_version=result.prompt_version,
                    data_snapshot_id=snapshot.id,
                )
            )
        session.commit()
    except Exception as exc:
        session.rollback()
        raise CommitteePersistenceError("Failed to persist AI committee results.") from exc


def _direction_from_recommendation(result: CommitteeStockResult) -> str:
    if result.recommendation == "STRONG_WATCH":
        return "BUY"
    if result.recommendation == "WATCH":
        return "WATCH"
    if result.recommendation in {"AVOID", "BLOCKED"}:
        return "AVOID"
    return "NEUTRAL"


def _expected_direction(result: CommitteeStockResult) -> str:
    if result.recommendation in {"STRONG_WATCH", "WATCH"}:
        return "UP"
    if result.recommendation in {"AVOID", "BLOCKED"}:
        return "DOWN"
    return "SIDEWAYS"
