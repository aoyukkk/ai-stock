from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from database.models.validation import ModelValidationLLMAudit, ModelValidationSample
from llm_gateway.schemas import LLMMessage, LLMRequest, LLMResponse
from llm_gateway.service import LLMGatewayService, get_llm_gateway_service
from stock_codes import normalize_ts_code


PRO_ALIAS = "controller-high-capability"
PRO_PROMPT_VERSION = "daily-pro-aggregation-v1"


class ProStockResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stock_code: str
    pro_score: float = Field(ge=0, le=100)
    priority: str
    final_summary: str
    key_strengths: list[str]
    key_risks: list[str]
    fundamental_quality: str
    quant_llm_consistency: str
    manual_review_priority: str
    data_conflict: bool


class ProAggregationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stocks: list[ProStockResult]
    portfolio_concentration_notes: list[str]


@dataclass(frozen=True)
class ProAggregationUsage:
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    repair_input_tokens: int = 0
    repair_output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class ProAggregationError(ValueError):
    def __init__(self, usage: ProAggregationUsage, audit: dict[str, Any]) -> None:
        self.usage = usage
        self.audit = audit
        super().__init__("PRO_AGGREGATION_SCHEMA_FAILED")


class ProAggregationService:
    def __init__(self, gateway: LLMGatewayService | None = None) -> None:
        self.gateway = gateway or get_llm_gateway_service()

    def run(
        self,
        samples: list[ModelValidationSample],
        *,
        selection_sources: dict[str, str],
        model_alias: str = PRO_ALIAS,
    ) -> tuple[ProAggregationResult, ProAggregationUsage, dict[str, Any]]:
        if not samples or len(samples) > 27:
            raise ValueError("PRO_INPUT_STOCK_COUNT_INVALID")
        compact = [self._compact_sample(sample, selection_sources) for sample in samples]
        expected_codes = {item["stock_code"] for item in compact}
        first = self.gateway.chat(self._request(compact, model_alias, repair=False))
        result = self._validate(first, expected_codes)
        repair_input = repair_output = 0
        response = first
        if result is None:
            second = self.gateway.chat(self._request(compact, model_alias, repair=True))
            repair_input = second.input_tokens
            repair_output = second.output_tokens
            result = self._validate(second, expected_codes)
            response = second
        if result is None:
            usage = self._usage(first, response, repair_input, repair_output)
            audit = self._audit(
                response, usage, len(samples), model_alias=model_alias,
                repair_attempted=response is not first,
                schema_status="PRO_AGGREGATION_SCHEMA_FAILED",
            )
            raise ProAggregationError(usage, audit)
        ordered = sorted(result.stocks, key=lambda item: (-item.pro_score, item.stock_code))
        result = result.model_copy(update={"stocks": ordered})
        usage = self._usage(first, response, repair_input, repair_output)
        audit = self._audit(
            response, usage, len(samples), model_alias=model_alias,
            repair_attempted=response is not first, schema_status="PASS",
        )
        return result, usage, audit

    @staticmethod
    def _usage(
        first: LLMResponse,
        response: LLMResponse,
        repair_input: int,
        repair_output: int,
    ) -> ProAggregationUsage:
        return ProAggregationUsage(
            input_tokens=first.input_tokens + (repair_input if repair_input else 0),
            output_tokens=first.output_tokens + (repair_output if repair_output else 0),
            cost_usd=float(first.cost_usd or 0) + (float(response.cost_usd or 0) if response is not first else 0),
            latency_ms=first.latency_ms + (response.latency_ms if response is not first else 0),
            repair_input_tokens=repair_input,
            repair_output_tokens=repair_output,
        )

    @staticmethod
    def _audit(
        response: LLMResponse,
        usage: ProAggregationUsage,
        sample_count: int,
        *,
        model_alias: str,
        repair_attempted: bool,
        schema_status: str,
    ) -> dict[str, Any]:
        return {
            "stock_code": "PORTFOLIO",
            "task": "daily_pro_aggregation",
            "knowledge_mode": "LLM_UNVERIFIED_CURRENT",
            "model_alias": response.model_alias or model_alias,
            "actual_model": response.model,
            "prompt_version": PRO_PROMPT_VERSION,
            "status": response.status,
            "schema_status": schema_status,
            "request_hash": response.request_hash,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cost_usd": usage.cost_usd,
            "latency_ms": usage.latency_ms,
            "cache_status": "HIT" if response.cached else "MISS",
            "error_category": "" if schema_status == "PASS" else schema_status,
            "error_field": "",
            "error_message": "",
            "diagnostics": {
                "input_stock_count": sample_count,
                "thinking_mode": response.thinking_mode,
                "reasoning_effort": response.reasoning_effort,
                "repair_attempted": repair_attempted,
                "reasoning_content_stored": False,
            },
        }

    @staticmethod
    def apply_to_samples(samples: list[ModelValidationSample], result: ProAggregationResult) -> None:
        by_code = {item.stock_code: item for item in result.stocks}
        for sample in samples:
            item = by_code[normalize_ts_code(sample.stock_code)]
            rank = result.stocks.index(item) + 1
            screening = dict(sample.screening_result)
            screening["_pro"] = {
                **item.model_dump(mode="json"),
                "pro_rank": rank,
                "portfolio_concentration_notes": result.portfolio_concentration_notes,
                "source_status": "LLM_UNVERIFIED",
            }
            sample.screening_result = screening

    @staticmethod
    def audit_row(validation_run_id: str, audit: dict[str, Any]) -> ModelValidationLLMAudit:
        return ModelValidationLLMAudit(validation_run_id=validation_run_id, **audit)

    @staticmethod
    def _compact_sample(sample: ModelValidationSample, sources: dict[str, str]) -> dict[str, Any]:
        code = normalize_ts_code(sample.stock_code)
        fundamental = sample.fundamental_result or {}
        screening = sample.screening_result or {}
        quant = sample.quant_scores or {}
        return {
            "stock_code": code,
            "stock_name": sample.stock_name,
            "selection_source": sources.get(code, "LLM_TOP20"),
            "quant_rank": sample.rank,
            "quant_score": quant.get("total_score"),
            "flash_score": screening.get("llm_score"),
            "flash_decision": screening.get("screening_decision"),
            "confidence": screening.get("confidence"),
            "fundamental_summary": _short(fundamental.get("main_business_summary")),
            "observation_rating": fundamental.get("observation_rating"),
            "financial_status": fundamental.get("financial_status"),
            "data_quality_score": screening.get("data_quality_score"),
            "data_conflict": bool(screening.get("data_conflict")),
            "unverified_count": _unverified_count(fundamental),
        }

    @staticmethod
    def _request(compact: list[dict[str, Any]], model_alias: str, *, repair: bool) -> LLMRequest:
        instruction = (
            "Return exactly one JSON object matching the supplied schema. "
            "Summarize and rank the supplied stocks only. Preserve all Quant/Flash/financial facts. "
            "Do not create prices, positions, URLs, news, announcements, customers, orders, market shares, or exact industry rankings. "
            "All qualitative conclusions are unverified and conservative."
        )
        if repair:
            instruction += " This is the only repair attempt; verify every required key and every stock code."
        return LLMRequest(
            agent_name="controller_agent",
            task="daily_pro_aggregation",
            task_type="committee_controller",
            task_tier="HIGH_IMPACT",
            model_alias=model_alias,
            messages=[
                LLMMessage(role="system", content=instruction),
                LLMMessage(role="user", content=json.dumps({"stocks": compact}, ensure_ascii=False, separators=(",", ":"))),
            ],
            max_tokens=3200,
            json_mode=True,
            response_schema=ProAggregationResult.model_json_schema(),
            allow_fallback=False,
            thinking_mode="enabled",
            reasoning_effort="max",
            prompt_version=PRO_PROMPT_VERSION,
            metadata={"knowledge_mode": "LLM_UNVERIFIED_CURRENT", "search_provider_enabled": False},
        )

    @staticmethod
    def _validate(response: LLMResponse, expected_codes: set[str]) -> ProAggregationResult | None:
        if response.status not in {"ok", "SUCCESS"}:
            return None
        payload = response.parsed_json or response.structured_output
        if payload is None:
            try:
                payload = json.loads(response.content)
            except (json.JSONDecodeError, TypeError):
                return None
        try:
            result = ProAggregationResult.model_validate(payload)
        except ValidationError:
            return None
        codes = [normalize_ts_code(item.stock_code) for item in result.stocks]
        if len(codes) != len(set(codes)) or set(codes) != expected_codes:
            return None
        if _contains_url(result.model_dump(mode="json")):
            return None
        normalized = [item.model_copy(update={"stock_code": normalize_ts_code(item.stock_code)}) for item in result.stocks]
        return result.model_copy(update={"stocks": normalized})


def _short(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("summary") or value.get("description") or ""
    return str(value or "信息不足*")[:500]


def _unverified_count(value: Any) -> int:
    if isinstance(value, dict):
        return int(str(value.get("source_status") or "").upper() == "LLM_UNVERIFIED") + sum(
            _unverified_count(item) for item in value.values()
        )
    if isinstance(value, list):
        return sum(_unverified_count(item) for item in value)
    return 0


def _contains_url(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_contains_url(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_url(item) for item in value)
    text = str(value or "").lower()
    return "http://" in text or "https://" in text or "www." in text
