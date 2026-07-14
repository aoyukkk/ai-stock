from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from backend.core.config_manager import ConfigManager
from llm_gateway.schemas import LLMMessage, LLMRequest, LLMResponse
from llm_gateway.service import LLMGatewayService, get_llm_gateway_service
from research.input_quality import summarize_structured_input
from research.knowledge_mode import LLMKnowledgeMode, validate_knowledge_mode
from research.output_boundary import scan_output_boundary
from research.wire_schemas import (
    FlashComponentWireV4,
    FundamentalEnrichmentWireV4,
    FundamentalInferenceWireV3,
    flash_component_v4_example,
    fundamental_v4_example,
    fundamental_v4_to_domain,
    fundamental_wire_example,
    fundamental_wire_to_domain,
)
from stock_codes import normalize_ts_code


FUNDAMENTAL_PROMPT_VERSION = "structured_fundamental_enrichment_v4"
SCREENING_PROMPT_VERSION = "structured_light_screening_v5"
MODEL_ALIAS = "light-screening-default"
SYSTEM_PROMPT = (
    "你只能使用用户提供的结构化输入，不得使用外部知识、当前新闻、排名、市场份额或URL。"
    "信息不足时使用UNKNOWN、INSUFFICIENT_DATA或空数组。只输出一个合法JSON对象，"
    "禁止Markdown代码围栏、前后缀、推理过程、BUY/SELL、价格、仓位和止损建议。"
)
UNVERIFIED_CURRENT_SYSTEM_PROMPT = (
    "优先使用用户提供的结构化事实。仅可对缺失的静态定性字段做保守推断，必须标记为LLM_UNVERIFIED；"
    "无法判断时使用UNKNOWN或INSUFFICIENT_DATA。不得声称联网，不得生成URL、今日新闻、公告编号、"
    "具体客户、订单金额、市场份额或精确行业排名，不得覆盖Tushare事实和规则财务状态。"
    "只输出一个合法JSON对象，禁止推理过程、BUY/SELL、价格、仓位和止损建议。"
)

T = TypeVar("T", bound=BaseModel)


class StructuredOutputValidationError(ValueError):
    def __init__(
        self, *, stock_code: str, task: str, prompt_version: str,
        field: str, category: str, detail: str,
    ) -> None:
        self.stock_code = stock_code
        self.task = task
        self.prompt_version = prompt_version
        self.field = field
        self.category = category
        self.detail = detail
        super().__init__(f"{category}:{stock_code}:{task}:{field}")

    def as_dict(self) -> dict[str, str]:
        return {
            "stock_code": self.stock_code, "task": self.task,
            "prompt_version": self.prompt_version, "field": self.field,
            "error_category": self.category, "detail": self.detail,
        }


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StructuredLightScreening(StrictModel):
    stock_code: str
    screening_decision: Literal["ADVANCE", "HOLD", "REJECT", "WATCH_ONLY"]
    llm_score: Decimal = Field(ge=0, le=100)
    confidence: Decimal = Field(ge=0, le=1)
    reason: str
    risk_note: str
    data_conflict: bool = False
    requires_manual_review: bool = True
    evidence_fields: list[str] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)
    fundamental_signal: str = "UNKNOWN"
    quant_consistency_signal: str = "UNKNOWN"
    financial_signal: str = "UNKNOWN"
    data_quality_score: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    data_quality_penalty: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    risk_penalty: Decimal = Field(default=Decimal("0"), ge=0, le=100)

    @model_validator(mode="after")
    def conflict_cannot_advance(self):
        if self.data_conflict and self.screening_decision == "ADVANCE":
            self.screening_decision = "HOLD"
            self.confidence = min(self.confidence, Decimal("0.4"))
            self.requires_manual_review = True
        return self


def screening_example(stock_code: str = "000000.SZ") -> dict[str, Any]:
    return {
        "stock_code": stock_code, "screening_decision": "WATCH_ONLY", "llm_score": 0,
        "confidence": 0.1, "reason": "输入不足，需人工复核。", "risk_note": "缺少当前新闻。",
        "data_conflict": False, "requires_manual_review": True,
        "evidence_fields": [], "missing_data": [],
        "fundamental_signal": "INSUFFICIENT_DATA",
        "quant_consistency_signal": "INSUFFICIENT_DATA",
        "financial_signal": "INSUFFICIENT_DATA",
        "data_quality_score": 0,
        "data_quality_penalty": 0,
        "risk_penalty": 0,
    }


class StructuredValidationProvider:
    """Structured-input-only DeepSeek calls with local parsing and strict validation."""

    def __init__(self, gateway: LLMGatewayService | None = None) -> None:
        self.gateway = gateway or get_llm_gateway_service()
        self.audit: list[dict[str, Any]] = []

    def fundamental(self, context: dict[str, Any], *, run_mode: str, use_real_llm: bool) -> dict[str, Any]:
        knowledge_mode = LLMKnowledgeMode(
            context.get("knowledge_mode") or LLMKnowledgeMode.STRUCTURED_INPUT_ONLY.value
        )
        validate_knowledge_mode(run_mode, knowledge_mode)
        quality = summarize_structured_input(context)
        context["input_quality"] = quality
        if quality["status"] != "PASS":
            raise StructuredOutputValidationError(
                stock_code=quality["stock_code"], task="fundamental_structured_inference",
                prompt_version=FUNDAMENTAL_PROMPT_VERSION, field="$.input_quality",
                category="INPUT_COVERAGE_INCOMPLETE", detail=",".join(quality["missing_required"]),
            )
        wire = self._call(
            context=context, task="fundamental_structured_inference",
            prompt_version=FUNDAMENTAL_PROMPT_VERSION, schema=FundamentalEnrichmentWireV4,
            example=fundamental_v4_example(quality["stock_code"]), max_tokens=self._max_tokens("fundamental", 3200),
            use_real_llm=use_real_llm,
        )
        if wire.get("_dry_run"):
            return self._dry_fundamental(context)
        return fundamental_v4_to_domain(FundamentalEnrichmentWireV4.model_validate(wire), context)

    def screening(self, context: dict[str, Any], *, run_mode: str, use_real_llm: bool) -> dict[str, Any]:
        knowledge_mode = LLMKnowledgeMode(
            context.get("knowledge_mode") or LLMKnowledgeMode.STRUCTURED_INPUT_ONLY.value
        )
        validate_knowledge_mode(run_mode, knowledge_mode)
        code = normalize_ts_code(str(context.get("stock_code") or ""))
        wire = self._call(
            context=context, task="structured_light_screening",
            prompt_version=SCREENING_PROMPT_VERSION, schema=FlashComponentWireV4,
            example=flash_component_v4_example(code), max_tokens=self._max_tokens("screening", 1400),
            use_real_llm=use_real_llm,
        )
        if wire.get("_dry_run"):
            return self._dry_screening(context)
        from research.flash_v4 import calculate_flash_v4, load_flash_scoring_config

        hard_risk = str((context.get("financial_status") or {}).get("status") or "NORMAL")
        return calculate_flash_v4(code, wire, load_flash_scoring_config(), hard_risk_status=hard_risk)

    def _call(
        self, *, context: dict[str, Any], task: str, prompt_version: str,
        schema: type[T], example: dict[str, Any], max_tokens: int, use_real_llm: bool,
    ) -> dict[str, Any]:
        if not use_real_llm:
            if schema is StructuredLightScreening:
                return self._dry_screening(context)
            if schema is FlashComponentWireV4:
                return {"_dry_run": True}
            return {"_dry_run": True}
        self._guard_real_calls()
        expected_code = normalize_ts_code(str(context.get("stock_code") or ""))
        prompt_payload = {
            "instruction": (
                "依据context填写与example键完全相同的一个JSON对象，不得增加键。"
                "Fundamental任务必须从主营和业务范围保守提取产品、宽泛产业链和结构性判断，不能统一UNKNOWN；"
                "Flash任务只评价0-100组件分，不得计算最终分数或决定。"
                "每个组件必须结合当前股票的quant、fundamental_inference、financial_status、"
                "missing_fields和input_quality独立评分；不得照抄example数值，不得把所有组件统一设为50。"
                "未知时降低对应分数并列入missing_data。"
            ),
            "example": example,
            "context": context,
        }
        response = self.gateway.chat(self._request(
            task=task, prompt_version=prompt_version, max_tokens=max_tokens,
            stock_code=expected_code, payload=prompt_payload,
            knowledge_mode=str(context.get("knowledge_mode") or LLMKnowledgeMode.STRUCTURED_INPUT_ONLY.value),
        ))
        diagnostics = _diagnose_response(response)
        category, field, detail, parsed = self._validate_response(response, schema, expected_code)
        if not category:
            category, field, detail = _flash_component_semantic_error(parsed, schema, example)
        original_category = category
        if category:
            diagnostics["repair_attempted"] = True
            diagnostics["repair_category"] = category
            _record_local_violation(diagnostics, category, field, detail)
            repair_payload = self._repair_payload(
                category=category, context=context, example=example, response=response,
            )
            repaired = self.gateway.chat(self._request(
                task=f"{task}_repair", prompt_version=prompt_version,
                max_tokens=max_tokens, stock_code=expected_code, payload=repair_payload,
                knowledge_mode=str(context.get("knowledge_mode") or LLMKnowledgeMode.STRUCTURED_INPUT_ONLY.value),
            ))
            repair_diagnostics = _diagnose_response(repaired)
            diagnostics["repair_input_tokens"] = repaired.input_tokens
            diagnostics["repair_output_tokens"] = repaired.output_tokens
            category, field, detail, parsed = self._validate_response(repaired, schema, expected_code)
            if not category:
                category, field, detail = _flash_component_semantic_error(parsed, schema, example)
            diagnostics["repair_response"] = repair_diagnostics
            response = _combined_response(response, repaired)
        else:
            diagnostics["repair_attempted"] = False
            diagnostics["repair_category"] = ""
            diagnostics["repair_input_tokens"] = 0
            diagnostics["repair_output_tokens"] = 0
            diagnostics["local_scanner_result"] = "PASS"

        if category:
            diagnostics["final_error_category"] = category
            self.audit.append(self._audit_row(
                context, task, prompt_version, response, category,
                field=field, detail=detail, diagnostics=diagnostics,
            ))
            raise StructuredOutputValidationError(
                stock_code=expected_code, task=task, prompt_version=prompt_version,
                field=field, category=category, detail=_compact_error(detail),
            )
        diagnostics["original_failure_category"] = original_category or ""
        diagnostics["final_error_category"] = "PASS"
        self.audit.append(self._audit_row(
            context, task, prompt_version, response, "PASS", diagnostics=diagnostics,
        ))
        assert parsed is not None
        return parsed.model_dump(mode="json")

    @staticmethod
    def _request(
        *, task: str, prompt_version: str, max_tokens: int,
        stock_code: str, payload: dict[str, Any], knowledge_mode: str,
    ) -> LLMRequest:
        system_prompt = (
            UNVERIFIED_CURRENT_SYSTEM_PROMPT
            if knowledge_mode == LLMKnowledgeMode.LLM_UNVERIFIED_CURRENT.value
            else SYSTEM_PROMPT
        )
        return LLMRequest(
            agent_name="model_validation", task=task, task_type=task,
            model_alias=MODEL_ALIAS,
            messages=[
                LLMMessage(role="system", content=system_prompt),
                LLMMessage(role="user", content=json.dumps(payload, ensure_ascii=False, default=str)[:32000]),
            ],
            max_tokens=max_tokens, prompt_version=prompt_version,
            response_schema=None, json_mode=False, thinking_mode="disabled", allow_fallback=False,
            metadata={
                "stock_code": stock_code,
                "knowledge_mode": knowledge_mode,
                "model_validation": True, "structured": True,
            },
        )

    @staticmethod
    def _validate_response(
        response: LLMResponse, schema: type[T], expected_code: str,
    ) -> tuple[str, str, str, T | None]:
        if response.status != "ok":
            return _error_category(response.status), "$", _compact_error(response.error), None
        if str(response.finish_reason or "").lower() in {"content_filter", "safety", "refusal"}:
            return "PROVIDER_CONTENT_POLICY_REFUSAL", "$", f"finish_reason={response.finish_reason}", None
        content = response.content or ""
        if not content.strip():
            return "EMPTY_JSON_CONTENT", "$", "Provider returned empty content.", None
        if str(response.finish_reason or "").lower() in {"length", "max_tokens"}:
            return "JSON_TRUNCATED", "$", f"finish_reason={response.finish_reason}", None
        if content.lstrip().startswith("```"):
            return "JSON_FENCE_VIOLATION", "$", "JSON must not be wrapped in a Markdown fence.", None
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            if isinstance(response.parsed_json, dict) and content == "safe-json":
                payload = response.parsed_json
            else:
                return _json_error_category(content, exc), "$", f"JSON decode error at {exc.pos}.", None
        if not isinstance(payload, dict):
            return "TOP_LEVEL_NOT_OBJECT", "$", "Top-level JSON value must be an object.", None
        violation = scan_output_boundary(payload, expected_stock_code=expected_code)
        if violation:
            return violation.category, violation.path, violation.rule, None
        payload["stock_code"] = expected_code
        try:
            return "", "", "", schema.model_validate(payload)
        except ValidationError as exc:
            first = exc.errors()[0]
            field = "$" + "".join(f"[{item}]" if isinstance(item, int) else f".{item}" for item in first.get("loc") or [])
            return "SCHEMA_ERROR", field or "$", str(first.get("type") or "pydantic_validation"), None

    @staticmethod
    def _repair_payload(
        *, category: str, context: dict[str, Any], example: dict[str, Any], response: LLMResponse,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "instruction": f"修复为与example键完全相同的单个合法JSON对象。原错误类别：{category}。只输出JSON。",
            "example": example,
        }
        if category == "UNSUPPORTED_CLAIM":
            payload["instruction"] += (
                "删除所有第一、领先、龙头、唯一供应商、市场份额、客户名称和订单陈述；"
                "改用可能、潜在、需核验等中性表达。"
            )
        if category in {
            "EMPTY_JSON_CONTENT", "JSON_TRUNCATED", "PROVIDER_CONTENT_POLICY_REFUSAL",
            "DEGENERATE_COMPONENT_RESPONSE",
        }:
            payload["context"] = context
        else:
            payload["candidate"] = (response.content or "")[:12000]
        return payload

    def _guard_real_calls(self) -> None:
        if not os.getenv("DEEPSEEK_API_KEY", "").strip():
            raise ValueError("DEEPSEEK_API_KEY_NOT_CONFIGURED")
        if not _flag("LLM_REAL_CALLS_ENABLED"):
            raise ValueError("LLM_REAL_CALLS_DISABLED")
        if not _flag("RUN_REAL_FUNDAMENTAL_RESEARCH"):
            raise ValueError("RUN_REAL_FUNDAMENTAL_RESEARCH_DISABLED")
        if getattr(getattr(self.gateway, "config", None), "mock_only", False):
            raise ValueError("REAL_LLM_GATEWAY_MOCK_ONLY")
        if getattr(getattr(self.gateway, "usage", None), "budget_exhausted", False):
            raise ValueError("REAL_LLM_BUDGET_BLOCKED")

    @staticmethod
    def _max_tokens(task: str, default: int) -> int:
        config = ConfigManager().get_llm_gateway_config().get("structured_validation", {})
        return int(config.get(f"{task}_max_tokens", default))

    @staticmethod
    def _audit_row(
        context: dict[str, Any], task: str, prompt_version: str,
        response: LLMResponse, schema_status: str, *, field: str = "",
        detail: str | None = None, diagnostics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "stock_code": normalize_ts_code(str(context.get("stock_code") or "")), "task": task,
            "knowledge_mode": str(
                context.get("knowledge_mode") or LLMKnowledgeMode.STRUCTURED_INPUT_ONLY.value
            ),
            "model_alias": response.model_alias or MODEL_ALIAS, "actual_model": response.model,
            "prompt_version": prompt_version, "status": response.status, "schema_status": schema_status,
            "request_hash": response.request_hash, "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens, "cost_usd": response.cost_usd,
            "latency_ms": response.latency_ms, "cache_status": "HIT" if response.cached else "MISS",
            "error_category": "" if schema_status == "PASS" else schema_status,
            "error_field": field, "error_message": _compact_error(detail),
            "diagnostics": diagnostics or {}, "created_at": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _dry_screening(context: dict[str, Any]) -> dict[str, Any]:
        code = normalize_ts_code(str(context.get("stock_code") or ""))
        return StructuredLightScreening(
            stock_code=code, screening_decision="WATCH_ONLY", llm_score=0, confidence=0,
            reason="DRY_RUN_NO_MODEL_CALL", risk_note="仅检查输入与门禁，未调用模型。",
            missing_data=list(context.get("missing_fields") or []),
        ).model_dump(mode="json")

    @staticmethod
    def _dry_fundamental(context: dict[str, Any]) -> dict[str, Any]:
        wire = FundamentalEnrichmentWireV4.model_validate(fundamental_v4_example(
            normalize_ts_code(str(context.get("stock_code") or ""))
        ))
        result = fundamental_v4_to_domain(wire, context)
        result["analysis_status"] = "DRY_RUN"
        return result


def _diagnose_response(response: LLMResponse) -> dict[str, Any]:
    content = response.content or ""
    stripped = content.strip()
    metadata = response.raw_response_metadata or {}
    return {
        "provider_http_status": metadata.get("http_status"),
        "provider_error_code": metadata.get("error_code"),
        "provider_request_id": metadata.get("response_id") or metadata.get("request_id"),
        "finish_reason": response.finish_reason,
        "content_length": len(content),
        "content_empty": not bool(stripped),
        "first_non_whitespace_character": stripped[:1],
        "last_non_whitespace_character": stripped[-1:] if stripped else "",
        "markdown_fence_detected": stripped.startswith("```") or stripped.endswith("```"),
        "json_object_detected": stripped.startswith("{") and stripped.endswith("}"),
        "brace_balance": content.count("{") - content.count("}"),
        "response_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest() if content else "",
        "local_scanner_result": "NOT_RUN",
        "violation_code": "", "violation_json_path": "", "violation_rule": "",
    }


def _flash_component_semantic_error(
    parsed: BaseModel | None,
    schema: type[BaseModel],
    example: dict[str, Any],
) -> tuple[str, str, str]:
    if parsed is None or schema is not FlashComponentWireV4:
        return "", "", ""
    fields = (
        "quant_consistency_score", "fundamental_quality_score", "financial_quality_score",
        "risk_fit_score", "data_quality_score",
    )
    payload = parsed.model_dump(mode="json")
    values = tuple(float(payload[field]) for field in fields)
    example_values = tuple(float(example[field]) for field in fields)
    if len(set(values)) == 1:
        return (
            "DEGENERATE_COMPONENT_RESPONSE", "$.quant_consistency_score",
            "All Flash component scores are identical.",
        )
    if values == example_values:
        return (
            "DEGENERATE_COMPONENT_RESPONSE", "$.quant_consistency_score",
            "Flash component scores copied the prompt example.",
        )
    return "", "", ""


def _combined_response(first: LLMResponse, second: LLMResponse) -> LLMResponse:
    return second.model_copy(update={
        "input_tokens": first.input_tokens + second.input_tokens,
        "output_tokens": first.output_tokens + second.output_tokens,
        "total_tokens": first.total_tokens + second.total_tokens,
        "cost_usd": (first.cost_usd or 0) + (second.cost_usd or 0),
        "latency_ms": first.latency_ms + second.latency_ms,
        "request_hash": second.request_hash or first.request_hash,
    })


def _json_error_category(content: str, exc: json.JSONDecodeError) -> str:
    stripped = content.strip()
    if stripped.startswith("["):
        return "TOP_LEVEL_NOT_OBJECT"
    try:
        _, end = json.JSONDecoder().raw_decode(stripped)
        tail = stripped[end:].strip()
        if tail.startswith("{"):
            try:
                _, second_end = json.JSONDecoder().raw_decode(tail)
                if not tail[second_end:].strip():
                    return "MULTIPLE_JSON_OBJECTS"
            except json.JSONDecodeError:
                pass
        if tail:
            return "JSON_TRAILING_CONTENT"
    except json.JSONDecodeError:
        pass
    if stripped.count("{") > 1 and stripped.count("}") > 1:
        return "MULTIPLE_JSON_OBJECTS"
    return "INVALID_JSON"


def _record_local_violation(diagnostics: dict[str, Any], category: str, field: str, rule: str) -> None:
    local_categories = {
        "OUTPUT_BOUNDARY_VIOLATION", "UNSUPPORTED_CLAIM", "FORBIDDEN_CURRENT_INFORMATION",
        "FABRICATED_URL", "STOCK_CODE_MISMATCH",
    }
    if category in local_categories:
        diagnostics["local_scanner_result"] = "BLOCKED"
        diagnostics["violation_code"] = category
        diagnostics["violation_json_path"] = field
        diagnostics["violation_rule"] = rule
    else:
        diagnostics["local_scanner_result"] = "PASS_OR_NOT_APPLICABLE"


def _flag(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on"}


def _error_category(status: str) -> str:
    return {
        "auth_failed": "AUTH_FAILED", "insufficient_balance": "INSUFFICIENT_BALANCE",
        "rate_limited": "RATE_LIMITED", "provider_error": "PROVIDER_ERROR",
        "model_not_available": "MODEL_NOT_AVAILABLE", "schema_error": "SCHEMA_ERROR",
        "budget_exceeded": "BUDGET_EXCEEDED", "not_configured": "NOT_CONFIGURED",
    }.get(status, f"LLM_CALL_FAILED:{status}")


def _compact_error(error: str | None) -> str:
    return re.sub(r"\s+", " ", str(error or "")).strip()[:240]
