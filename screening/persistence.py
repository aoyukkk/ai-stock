from __future__ import annotations

from datetime import timedelta

from database.models.ai import (
    AIAnalysisResult,
    DecisionSnapshot,
    PredictionRecord,
    StockAIScore,
)
from database.models.system import PromptVersion
from llm_gateway.prompt_manager import content_hash
from llm_gateway.schemas import LLMResponse
from screening.exceptions import LightScreeningPersistenceError
from screening.real_prompt import PROMPT_VERSION, SYSTEM_PROMPT
from screening.schemas import (
    LightScreeningRanking,
    LightScreeningResult,
    RealLightScreeningInput,
    RealLightScreeningOutput,
)


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
                    "data_conflict": result.data_conflict,
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


def persist_real_screening_sample(
    session,
    item: RealLightScreeningInput,
    output: RealLightScreeningOutput,
    response: LLMResponse,
    *,
    run_id: str,
    is_real: bool,
) -> None:
    try:
        _ensure_prompt_version(session)
        snapshot = DecisionSnapshot(
            stock_code=item.stock_code,
            snapshot_time=_parse_snapshot_time(item.snapshot_time),
            market_data_json={
                "trade_date": item.trade_date,
                "limit_status": item.limit_status,
                "near_limit_up": item.near_limit_up,
                "near_limit_down": item.near_limit_down,
            },
            factor_json={
                "quant_run_id": item.quant_run_id,
                "quant_rank": item.quant_rank,
                "quant_score": str(item.quant_score),
                "scores": {
                    "technical": str(item.technical_score),
                    "capital": str(item.capital_score),
                    "emotion": str(item.emotion_score),
                    "momentum": str(item.momentum_score),
                    "risk": str(item.risk_score),
                },
                "factor_detail_summary": item.factor_detail_summary,
                "data_coverage": item.data_coverage,
                "known_missing_fields": item.known_missing_fields,
                "adjusted_technical_factor_applied": item.adjusted_technical_factor_applied,
                "limit_risk_weight_applied": item.limit_risk_weight_applied,
            },
            news_json={"available": item.news_available},
            agent_result_json={
                "run_id": run_id,
                "task_type": response.task_type,
                "task_tier": response.task_tier,
                "model_alias": response.model_alias,
                "provider": response.provider,
                "is_real": is_real,
                "cached": response.cached,
                "request_hash": response.request_hash,
                "prompt_version": PROMPT_VERSION,
                "structured_result": output.model_dump(mode="json"),
            },
            memory_json={},
            order_price_json={},
            final_score=output.llm_score,
            risk_level="HIGH" if output.screening_decision in {"REJECT", "WATCH_ONLY"} else "MEDIUM",
            recommendation="NEUTRAL",
        )
        session.add(snapshot)
        session.flush()
        session.add(
            AIAnalysisResult(
                stock_code=item.stock_code,
                agent_name="light_screening_agent",
                model_name=response.model,
                model_alias=response.model_alias,
                task_tier=response.task_tier,
                request_hash=response.request_hash,
                is_real=is_real,
                structured_result_json=output.model_dump(mode="json"),
                score=output.llm_score,
                direction="NEUTRAL",
                confidence=output.confidence,
                reason=output.reason,
                risk_note=output.risk_note,
                analysis_time=_parse_snapshot_time(item.snapshot_time),
                prompt_version=PROMPT_VERSION,
                model_version=response.model,
                llm_usage_id=response.usage_id,
            )
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        raise LightScreeningPersistenceError("Failed to persist real screening sample.") from exc


def _ensure_prompt_version(session) -> None:
    from sqlalchemy import select

    existing = session.scalar(
        select(PromptVersion).where(
            PromptVersion.agent_name == "light_screening_agent",
            PromptVersion.version == PROMPT_VERSION,
        )
    )
    if existing is None:
        session.add(
            PromptVersion(
                agent_name="light_screening_agent",
                version=PROMPT_VERSION,
                content_hash=content_hash(SYSTEM_PROMPT),
                description="Guarded V0.4 real small-sample light-screening prompt.",
                is_active=True,
            )
        )


def _parse_snapshot_time(value: str):
    from datetime import datetime, timezone

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


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
