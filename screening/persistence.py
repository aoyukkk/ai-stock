from __future__ import annotations

from datetime import timedelta

from database.models.ai import (
    AIAnalysisResult,
    DecisionSnapshot,
    PredictionRecord,
    StockAIScore,
)
from screening.exceptions import LightScreeningPersistenceError
from screening.schemas import LightScreeningRanking, LightScreeningResult


def persist_light_screening_results(session, ranking: LightScreeningRanking) -> None:
    try:
        for result in ranking.results:
            snapshot = DecisionSnapshot(
                stock_code=result.stock_code,
                snapshot_time=ranking.generated_at,
                market_data_json={},
                factor_json={
                    "quant_rank": result.quant_rank,
                    "quant_total_score": str(result.quant_total_score),
                },
                news_json={},
                agent_result_json={
                    "agent_name": "light_screening_agent",
                    "llm_score": str(result.llm_score),
                    "final_light_score": str(result.final_light_score),
                    "direction": result.direction,
                    "confidence": str(result.confidence),
                    "should_keep": result.should_keep,
                    "request_hash": result.request_hash,
                },
                memory_json={},
                order_price_json={},
                final_score=result.final_light_score,
                risk_level=_risk_level(result),
                recommendation=_recommendation(result),
            )
            session.add(snapshot)
            session.flush()

            session.add(
                AIAnalysisResult(
                    stock_code=result.stock_code,
                    agent_name="light_screening_agent",
                    model_name=result.model_name,
                    score=result.final_light_score,
                    direction=result.direction,
                    confidence=result.confidence,
                    reason=result.reason,
                    risk_note=result.risk_note,
                    analysis_time=ranking.generated_at,
                    prompt_version=result.prompt_version,
                    model_version=result.model_name,
                )
            )
            session.add(
                StockAIScore(
                    stock_code=result.stock_code,
                    time=ranking.generated_at,
                    final_score=result.final_light_score,
                    recommendation=_recommendation(result),
                    risk_level=_risk_level(result),
                    confidence=result.confidence,
                    controller_reason=result.reason,
                    decision_snapshot_id=snapshot.id,
                )
            )
            session.add(
                PredictionRecord(
                    stock_code=result.stock_code,
                    prediction_time=ranking.generated_at,
                    prediction_horizon_days=1,
                    expected_direction=_expected_direction(result),
                    score=result.final_light_score,
                    confidence=result.confidence,
                    recommendation=_recommendation(result),
                    reason=result.reason,
                    valid_until=ranking.generated_at + timedelta(days=1),
                    model_version=result.model_name,
                    prompt_version=result.prompt_version,
                    data_snapshot_id=snapshot.id,
                )
            )
        session.commit()
    except Exception as exc:
        session.rollback()
        raise LightScreeningPersistenceError("Failed to persist light screening results.") from exc


def _recommendation(result: LightScreeningResult) -> str:
    if not result.should_keep or result.direction == "AVOID":
        return "AVOID"
    if result.direction == "BUY":
        return "STRONG_WATCH"
    if result.direction == "WATCH":
        return "WATCH"
    return "NEUTRAL"


def _expected_direction(result: LightScreeningResult) -> str:
    if result.direction in {"BUY", "WATCH"}:
        return "UP"
    if result.direction == "AVOID":
        return "DOWN"
    return "SIDEWAYS"


def _risk_level(result: LightScreeningResult) -> str:
    if not result.should_keep:
        return "HIGH"
    if "risk" in result.risk_note.lower() and result.final_light_score < 50:
        return "MEDIUM"
    return "LOW"
