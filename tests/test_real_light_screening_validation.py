from datetime import date
from decimal import Decimal

from sqlalchemy import select

import database.models  # noqa: F401
from database.base import Base
from database.models.ai import AIAnalysisResult, DecisionSnapshot, PredictionRecord
from database.models.factor import StockFactorScore
from database.models.system import PromptVersion
from database.session import create_engine_from_url, get_session
from llm_gateway.schemas import LLMResponse
from screening.real_validation import RealLightScreeningValidationService


def _session_with_scores(count=5):
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    for index in range(count):
        score = Decimal(str(90 - index * 10))
        session.add(
            StockFactorScore(
                stock_code=f"00000{index + 1}",
                date=date(2026, 7, 10),
                technical_score=score,
                capital_score=score,
                emotion_score=score,
                momentum_score=score,
                risk_score=score,
                total_score=score,
                factor_version="test",
            )
        )
    session.commit()
    return engine, session


class NoCallGateway:
    def __init__(self, **kwargs):
        self.usage = type("Usage", (), {"budget_exhausted": False})()

    def preview_route(self, task_type, model_alias=None):
        return {
            "task_type": task_type,
            "task_tier": "SIMPLE",
            "model_alias": "light-screening-default",
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "thinking_mode": "disabled",
            "reasoning_effort": None,
        }

    def chat(self, request):
        raise AssertionError("dry-run or blocked run must not call a model")


class SuccessfulRealGateway(NoCallGateway):
    def check_model_availability(self, task_type, model_alias=None):
        return {"available": True}

    def chat(self, request):
        item = request.metadata["real_screening_input"]
        parsed = {
            "stock_code": item["stock_code"],
            "quant_rank": item["quant_rank"],
            "screening_decision": "HOLD",
            "llm_score": 70,
            "short_term_opportunity": 70,
            "factor_consistency": 70,
            "capital_confirmation": 70,
            "emotion_confirmation": 70,
            "risk_score": 60,
            "data_quality_score": 60,
            "confidence": 0.7,
            "reason": "Based on supplied quant fields only.",
            "risk_note": "News is unavailable.",
            "data_conflict": False,
            "missing_data": ["news_summary"],
            "evidence_fields": ["quant_score", "risk_score", "known_missing_fields"],
        }
        return LLMResponse(
            provider="deepseek",
            model="deepseek-v4-flash",
            model_alias="light-screening-default",
            task_type="real_light_screening_sample",
            task_tier="SIMPLE",
            thinking_mode="disabled",
            content="safe-json",
            parsed_json=parsed,
            structured_output=parsed,
            input_tokens=20,
            input_cache_miss_tokens=20,
            output_tokens=10,
            total_tokens=30,
            cost_usd=0.00001,
            cost_status="CALCULATED",
            pricing_version="fixture",
            latency_ms=12,
            request_hash=f"hash-{item['stock_code']}",
            prompt_version="light_screening_v0_4",
            status="ok",
            usage_id=None,
        )


def test_stratified_dry_run_is_deterministic_and_zero_call() -> None:
    engine, session = _session_with_scores()
    try:
        result = RealLightScreeningValidationService(
            session=session, gateway_factory=NoCallGateway
        ).run(
            quant_run_id=None,
            sample_mode="STRATIFIED",
            sample_size=3,
            dry_run=True,
            use_real_provider=False,
        )

        assert result.status == "DRY_RUN"
        assert [item["quant_rank"] for item in result.selected_stocks] == [1, 3, 5]
        assert result.route["model"] == "deepseek-v4-flash"
        assert result.route["thinking_mode"] == "disabled"
        assert result.results == []
    finally:
        session.close()
        engine.dispose()


def test_top_n_dry_run_is_deterministic() -> None:
    engine, session = _session_with_scores()
    try:
        result = RealLightScreeningValidationService(
            session=session, gateway_factory=NoCallGateway
        ).run(
            quant_run_id="run-1",
            sample_mode="TOP_N",
            sample_size=2,
            dry_run=True,
            use_real_provider=False,
        )
        assert [item["quant_rank"] for item in result.selected_stocks] == [1, 2]
        assert result.quant_run_id == "run-1"
    finally:
        session.close()
        engine.dispose()


def test_real_request_is_blocked_when_explicit_flags_are_off(monkeypatch) -> None:
    monkeypatch.setenv("LLM_REAL_CALLS_ENABLED", "false")
    monkeypatch.setenv("RUN_REAL_LLM_SCREENING", "false")
    engine, session = _session_with_scores(1)
    try:
        result = RealLightScreeningValidationService(
            session=session, gateway_factory=NoCallGateway
        ).run(
            quant_run_id=None,
            sample_mode="TOP_N",
            sample_size=1,
            dry_run=False,
            use_real_provider=True,
        )
        assert result.status == "REAL_CALL_DISABLED"
        assert result.results == []
    finally:
        session.close()
        engine.dispose()


def test_real_success_persists_screening_and_snapshot_without_prediction(monkeypatch) -> None:
    engine, session = _session_with_scores(1)
    service = RealLightScreeningValidationService(
        session=session, gateway_factory=SuccessfulRealGateway
    )
    monkeypatch.setattr(service, "_readiness", lambda gateway, use_real: "READY")
    try:
        result = service.run(
            quant_run_id="quant-test",
            sample_mode="TOP_N",
            sample_size=1,
            dry_run=False,
            use_real_provider=True,
        )

        assert result.status == "SUCCESS"
        assert result.results[0].screening_decision == "HOLD"
        assert result.results[0].model_alias == "light-screening-default"
        assert len(session.scalars(select(AIAnalysisResult)).all()) == 1
        assert len(session.scalars(select(DecisionSnapshot)).all()) == 1
        assert len(session.scalars(select(PromptVersion)).all()) == 1
        assert len(session.scalars(select(PredictionRecord)).all()) == 0
        analysis = session.scalar(select(AIAnalysisResult))
        assert analysis is not None and analysis.is_real is True
        assert analysis.structured_result_json["screening_decision"] == "HOLD"
    finally:
        session.close()
        engine.dispose()
