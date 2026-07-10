from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy import func, select

from backend.core.config_manager import ConfigManager
from database.models.factor import StockFactorDetail, StockFactorScore
from database.models.stock import StockMaster
from database.session import get_session
from llm_gateway.exceptions import LLMGatewayError, LLMModelNotAvailableError
from llm_gateway.schemas import LLMMessage, LLMRequest, LLMResponse
from llm_gateway.service import LLMGatewayService
from quant.service import QuantService
from screening.persistence import persist_real_screening_sample
from screening.real_prompt import OUTPUT_SCHEMA, PROMPT_VERSION, SYSTEM_PROMPT, build_real_screening_prompt
from screening.schemas import (
    RealLightScreeningInput,
    RealLightScreeningOutput,
    RealScreeningRunResult,
    RealScreeningSampleResult,
    SampleMode,
)


class RealLightScreeningValidationService:
    def __init__(
        self,
        *,
        session=None,
        quant_service: QuantService | None = None,
        gateway_factory: Callable[..., LLMGatewayService] = LLMGatewayService,
    ) -> None:
        self.session = session
        self.quant_service = quant_service or QuantService()
        self.gateway_factory = gateway_factory

    def run(
        self,
        *,
        quant_run_id: str | None,
        sample_mode: SampleMode,
        sample_size: int,
        dry_run: bool,
        use_real_provider: bool,
    ) -> RealScreeningRunResult:
        hard_max = min(5, max(1, int(os.getenv("REAL_LLM_SAMPLE_MAX", "5"))))
        if sample_size > hard_max:
            raise ValueError(f"sample_size must not exceed {hard_max}")
        own_session = self.session is None
        session = self.session or get_session()
        try:
            candidates, source, resolved_quant_run_id = self._load_candidates(session, quant_run_id)
            selected = _select_samples(candidates, sample_size, sample_mode)
            gateway = self.gateway_factory(db_session=session)
            route = gateway.preview_route("real_light_screening_sample")
            readiness = self._readiness(gateway, use_real_provider)
            run_id = f"llm-screening-{uuid.uuid4().hex[:12]}"
            selected_summary = [
                {"stock_code": item.stock_code, "quant_rank": item.quant_rank, "quant_score": float(item.quant_score)}
                for item in selected
            ]
            if dry_run:
                return _run_result(
                    status="DRY_RUN",
                    dry_run=True,
                    sample_mode=sample_mode,
                    selected=selected,
                    quant_run_id=resolved_quant_run_id,
                    source=source,
                    route=route,
                    readiness=readiness,
                    real_or_mock="DRY_RUN",
                    run_id=run_id,
                    selected_summary=selected_summary,
                    results=[],
                )

            if use_real_provider and readiness != "READY":
                return _run_result(
                    status=readiness,
                    dry_run=False,
                    sample_mode=sample_mode,
                    selected=selected,
                    quant_run_id=resolved_quant_run_id,
                    source=source,
                    route=route,
                    readiness=readiness,
                    real_or_mock="REAL",
                    run_id=run_id,
                    selected_summary=selected_summary,
                    results=[],
                )

            if use_real_provider:
                try:
                    gateway.check_model_availability("real_light_screening_sample")
                except LLMModelNotAvailableError:
                    return _run_result(
                        status="MODEL_NOT_AVAILABLE", dry_run=False, sample_mode=sample_mode,
                        selected=selected, quant_run_id=resolved_quant_run_id, source=source,
                        route=route, readiness="MODEL_NOT_AVAILABLE", real_or_mock="REAL",
                        run_id=run_id, selected_summary=selected_summary, results=[],
                    )
                except LLMGatewayError:
                    return _run_result(
                        status="PROVIDER_ERROR", dry_run=False, sample_mode=sample_mode,
                        selected=selected, quant_run_id=resolved_quant_run_id, source=source,
                        route=route, readiness="PROVIDER_ERROR", real_or_mock="REAL",
                        run_id=run_id, selected_summary=selected_summary, results=[],
                    )

            results: list[RealScreeningSampleResult] = []
            for item in selected:
                response = gateway.chat(self._request(item, use_real_provider))
                output = self._parse_or_safe_output(item, response)
                if use_real_provider:
                    persist_real_screening_sample(
                        session, item, output, response, run_id=run_id, is_real=True
                    )
                results.append(_sample_result(item, output, response))

            status = "SUCCESS" if all(item.status == "ok" for item in results) else "PARTIAL_SUCCESS"
            return _run_result(
                status=status,
                dry_run=False,
                sample_mode=sample_mode,
                selected=selected,
                quant_run_id=resolved_quant_run_id,
                source=source,
                route=route,
                readiness=readiness,
                real_or_mock="REAL" if use_real_provider else "MOCK",
                run_id=run_id,
                selected_summary=selected_summary,
                results=results,
            )
        finally:
            if own_session:
                session.close()

    def _readiness(self, gateway: LLMGatewayService, use_real_provider: bool) -> str:
        if not use_real_provider:
            return "MOCK_READY"
        if not _env_enabled("LLM_REAL_CALLS_ENABLED") or not _env_enabled("RUN_REAL_LLM_SCREENING"):
            return "REAL_CALL_DISABLED"
        config = ConfigManager().get_llm_gateway_config().get("llm", {})
        if bool(config.get("mock_only", True)):
            return "MOCK_ONLY"
        provider = config.get("providers", {}).get("deepseek", {})
        key_env = str(provider.get("api_key_env") or "DEEPSEEK_API_KEY")
        if not os.getenv(key_env, "").strip():
            return "KEY_NOT_CONFIGURED"
        if gateway.usage.budget_exhausted:
            return "BUDGET_BLOCKED"
        return "READY"

    def _request(self, item: RealLightScreeningInput, use_real_provider: bool) -> LLMRequest:
        return LLMRequest(
            agent_name="light_screening_agent",
            task="real_light_screening_sample",
            task_type="real_light_screening_sample",
            messages=[
                LLMMessage(role="system", content=SYSTEM_PROMPT),
                LLMMessage(role="user", content=build_real_screening_prompt(item)),
            ],
            prompt_version=PROMPT_VERSION,
            metadata={
                "structured": True,
                "explicit_real_screening": use_real_provider,
                "real_screening_input": item.model_dump(mode="json"),
                "quant_run_id": item.quant_run_id,
                "stock_code": item.stock_code,
            },
            response_schema=OUTPUT_SCHEMA,
            json_mode=True,
            allow_fallback=not use_real_provider,
        )

    def _parse_or_safe_output(
        self, item: RealLightScreeningInput, response: LLMResponse
    ) -> RealLightScreeningOutput:
        if response.status != "ok" or response.parsed_json is None:
            return _safe_output(item, f"Gateway status: {response.status}")
        try:
            output = RealLightScreeningOutput.model_validate(response.parsed_json)
        except Exception:
            return _safe_output(item, "Structured result validation failed.")
        allowed_fields = set(RealLightScreeningInput.model_fields)
        if output.stock_code != item.stock_code:
            return _safe_output(item, "Output stock_code mismatch.")
        if set(output.evidence_fields) - allowed_fields:
            return _safe_output(item, "Output referenced unknown evidence fields.")
        if output.data_conflict and output.screening_decision == "ADVANCE":
            return _safe_output(item, "Conflicted data cannot advance.")
        return output.model_copy(
            update={"missing_data": sorted(set(output.missing_data) | set(item.known_missing_fields))}
        )

    def _load_candidates(
        self, session, quant_run_id: str | None
    ) -> tuple[list[RealLightScreeningInput], str, str]:
        latest_date = session.scalar(select(func.max(StockFactorScore.date)))
        if latest_date is not None:
            scores = session.scalars(
                select(StockFactorScore)
                .where(StockFactorScore.date == latest_date)
                .order_by(StockFactorScore.total_score.desc(), StockFactorScore.stock_code.asc())
            ).all()
            if scores:
                return (
                    self._inputs_from_database(session, scores, latest_date, quant_run_id),
                    "database",
                    quant_run_id or f"stock-factor-{latest_date.isoformat()}",
                )

        ranking = self.quant_service.run_quant_scan(top_q=50, persist=False)
        run_id = quant_run_id or f"quant-service-{ranking.generated_at.isoformat()}"
        inputs = [
            _input_from_quant_result(item, ranking.generated_at, run_id)
            for item in ranking.results
        ]
        return inputs, "quant_service", run_id

    def _inputs_from_database(self, session, scores, latest_date, quant_run_id):
        codes = [row.stock_code for row in scores]
        masters = {
            row.code: row
            for row in session.scalars(select(StockMaster).where(StockMaster.code.in_(codes))).all()
        }
        detail_rows = session.scalars(
            select(StockFactorDetail).where(
                StockFactorDetail.date == latest_date,
                StockFactorDetail.stock_code.in_(codes),
            )
        ).all()
        details_by_code: dict[str, list[Any]] = {}
        for detail in detail_rows:
            details_by_code.setdefault(detail.stock_code, []).append(detail)
        run_id = quant_run_id or f"stock-factor-{latest_date.isoformat()}"
        snapshot = datetime.now(timezone.utc).isoformat()
        return [
            _input_from_database_row(
                row, masters.get(row.stock_code), details_by_code.get(row.stock_code, []),
                rank=index, run_id=run_id, snapshot=snapshot,
            )
            for index, row in enumerate(scores, start=1)
        ]


def _select_samples(items, sample_size: int, sample_mode: SampleMode):
    if not items:
        return []
    count = min(sample_size, len(items))
    if sample_mode == "TOP_N" or count == 1:
        return list(items[:count])
    indexes = [round(index * (len(items) - 1) / (count - 1)) for index in range(count)]
    return [items[index] for index in indexes]


def _input_from_database_row(row, master, details, *, rank, run_id, snapshot):
    detail_map = {item.factor_name: item for item in details}
    summaries = [_detail_summary(item) for item in sorted(details, key=lambda value: value.factor_name)[:12]]
    missing = ["news_summary"]
    if not details:
        missing.append("factor_detail_summary")
    return RealLightScreeningInput(
        stock_code=row.stock_code,
        stock_name=master.name if master else row.stock_code,
        trade_date=row.date.isoformat(),
        quant_run_id=run_id,
        quant_rank=rank,
        quant_score=row.total_score or Decimal("0"),
        technical_score=row.technical_score or Decimal("0"),
        capital_score=row.capital_score or Decimal("0"),
        emotion_score=row.emotion_score or Decimal("0"),
        momentum_score=row.momentum_score or Decimal("0"),
        risk_score=row.risk_score or Decimal("0"),
        technical_summary=_score_summary("technical", row.technical_score),
        capital_summary=_score_summary("capital", row.capital_score),
        emotion_summary=_score_summary("emotion", row.emotion_score),
        momentum_summary=_score_summary("momentum", row.momentum_score),
        risk_summary=_score_summary("risk", row.risk_score),
        factor_detail_summary=summaries,
        limit_status=_detail_text(detail_map.get("limit_status"), "UNKNOWN"),
        near_limit_up=None,
        near_limit_down=None,
        consecutive_limit_up=_detail_int(detail_map.get("consecutive_limit_up_count")),
        data_coverage={
            "factor_detail_count": len(details),
            "adj_factor": _detail_bool(detail_map.get("adj_factor_available")),
            "stk_limit": "limit_status" in detail_map,
        },
        data_quality="PARTIAL" if details else "LIMITED",
        data_sources=["database:stock_factor_score"],
        snapshot_time=snapshot,
        known_missing_fields=missing,
        adj_factor_coverage=_detail_bool(detail_map.get("adj_factor_available")),
        adjusted_technical_factor_applied=False,
        limit_risk_explanation_available="limit_status" in detail_map,
        limit_risk_weight_applied=False,
    )


def _input_from_quant_result(item, generated_at, run_id):
    detail_map = {detail.factor_name: detail for detail in item.factor_details}
    return RealLightScreeningInput(
        stock_code=item.stock_code,
        stock_name=item.stock_name,
        trade_date=generated_at.date().isoformat(),
        quant_run_id=run_id,
        quant_rank=int(item.rank or 0),
        quant_score=item.total_score,
        technical_score=item.technical_score,
        capital_score=item.capital_score,
        emotion_score=item.emotion_score,
        momentum_score=item.momentum_score,
        risk_score=item.risk_score,
        technical_summary=_score_summary("technical", item.technical_score),
        capital_summary=_score_summary("capital", item.capital_score),
        emotion_summary=_score_summary("emotion", item.emotion_score),
        momentum_summary=_score_summary("momentum", item.momentum_score),
        risk_summary=_score_summary("risk", item.risk_score),
        factor_detail_summary=[_detail_summary(detail) for detail in item.factor_details[:12]],
        limit_status=_detail_text(detail_map.get("limit_status"), "UNKNOWN"),
        near_limit_up=None,
        near_limit_down=None,
        consecutive_limit_up=_detail_int(detail_map.get("consecutive_limit_up_count")),
        data_coverage={"factor_detail_count": len(item.factor_details)},
        data_quality="PARTIAL",
        data_sources=["quant_service"],
        snapshot_time=generated_at.isoformat(),
        known_missing_fields=["news_summary"],
        adj_factor_coverage=_detail_bool(detail_map.get("adj_factor_available")),
        adjusted_technical_factor_applied=False,
        limit_risk_explanation_available="limit_status" in detail_map,
        limit_risk_weight_applied=False,
    )


def _detail_summary(detail) -> dict[str, Any]:
    return {
        "factor_group": detail.factor_group,
        "factor_name": detail.factor_name,
        "raw_value": str(detail.raw_value) if detail.raw_value is not None else None,
        "score": str(detail.score) if detail.score is not None else None,
        "explain_text": str(detail.explain_text or "")[:160],
    }


def _detail_bool(detail) -> bool:
    return bool(detail and detail.raw_value is not None and Decimal(str(detail.raw_value)) > 0)


def _detail_int(detail) -> int | None:
    return int(detail.raw_value) if detail and detail.raw_value is not None else None


def _detail_text(detail, default: str) -> str:
    return str(detail.explain_text) if detail and detail.explain_text else default


def _score_summary(name: str, score) -> str:
    return f"{name}_score={Decimal(str(score or 0)).quantize(Decimal('0.0001'))}"


def _safe_output(item: RealLightScreeningInput, reason: str) -> RealLightScreeningOutput:
    return RealLightScreeningOutput(
        stock_code=item.stock_code,
        quant_rank=item.quant_rank,
        screening_decision="WATCH_ONLY",
        llm_score=Decimal("0"),
        short_term_opportunity=Decimal("0"),
        factor_consistency=Decimal("0"),
        capital_confirmation=Decimal("0"),
        emotion_confirmation=Decimal("0"),
        risk_score=Decimal("100"),
        data_quality_score=Decimal("0"),
        confidence=Decimal("0"),
        reason=reason,
        risk_note="Safe degradation requires manual review.",
        data_conflict=True,
        missing_data=item.known_missing_fields,
        evidence_fields=[],
    )


def _sample_result(item, output, response):
    return RealScreeningSampleResult(
        stock_code=item.stock_code,
        quant_rank=item.quant_rank,
        quant_score=item.quant_score,
        screening_decision=output.screening_decision,
        llm_score=output.llm_score,
        confidence=output.confidence,
        data_conflict=output.data_conflict,
        missing_data=output.missing_data,
        provider=response.provider,
        model_alias=response.model_alias or "unknown",
        latency_ms=response.latency_ms,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        cost_usd=response.cost_usd,
        cost_status=response.cost_status,
        cached=response.cached,
        status=response.status,
    )


def _run_result(*, status, dry_run, sample_mode, selected, quant_run_id, source, route, readiness, real_or_mock, run_id, selected_summary, results):
    cost_values = [item.cost_usd for item in results if item.cost_usd is not None]
    return RealScreeningRunResult(
        status=status,
        dry_run=dry_run,
        sample_mode=sample_mode,
        sample_size=len(selected),
        quant_run_id=quant_run_id,
        sample_source=source,
        input_fields=sorted(RealLightScreeningInput.model_fields),
        route=route,
        real_call_readiness=readiness,
        real_or_mock=real_or_mock,
        run_id=run_id,
        selected_stocks=selected_summary,
        results=results,
        aggregate_usage={
            "input_tokens": sum(item.input_tokens for item in results),
            "output_tokens": sum(item.output_tokens for item in results),
            "total_tokens": sum(item.input_tokens + item.output_tokens for item in results),
        },
        aggregate_cost_usd=round(sum(cost_values), 8) if cost_values else None,
        aggregate_cost_status="CALCULATED" if results and len(cost_values) == len(results) else "COST_NOT_CONFIGURED",
        cache_status={
            "hit_count": sum(1 for item in results if item.cached),
            "miss_count": sum(1 for item in results if not item.cached),
        },
    )


def _env_enabled(name: str) -> bool:
    return str(os.getenv(name, "")).strip().lower() in {"1", "true", "yes", "on"}
