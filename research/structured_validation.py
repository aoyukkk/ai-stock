from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from llm_gateway.schemas import LLMMessage, LLMRequest, LLMResponse
from llm_gateway.service import LLMGatewayService, get_llm_gateway_service
from research.inference_schemas import FundamentalInference
from research.knowledge_mode import LLMKnowledgeMode, validate_knowledge_mode


FUNDAMENTAL_PROMPT_VERSION = "structured_fundamental_validation_v1"
SCREENING_PROMPT_VERSION = "structured_light_screening_validation_v1"
MODEL_ALIAS = "light-screening-default"
SYSTEM_PROMPT = """你正在执行严格的历史点时结构化分析。
你只能使用用户输入中明确提供的数据，不得调用或使用模型内部关于该公司的外部事实知识。
不得补充输入中不存在的客户、供应商、订单、排名、市场份额、政策、新闻、产品型号或行业事件。
不得使用“目前”“今日”“最新”“近期消息显示”等表达，不得声称联网，不得输出URL或公告编号。
不能从输入直接归纳或保守推导的字段必须输出UNKNOWN或INSUFFICIENT_DATA。
不得输出BUY/SELL、价格、仓位、股数、止损价或reasoning_content。只输出符合JSON Schema的JSON。"""


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

    @model_validator(mode="after")
    def conflict_cannot_advance(self):
        if self.data_conflict and self.screening_decision == "ADVANCE":
            self.screening_decision = "HOLD"
            self.confidence = min(self.confidence, Decimal("0.4"))
            self.requires_manual_review = True
        return self


class StructuredValidationProvider:
    """Two-task provider that can only reach DeepSeek through the shared gateway."""

    def __init__(self, gateway: LLMGatewayService | None = None) -> None:
        self.gateway = gateway or get_llm_gateway_service()
        self.audit: list[dict[str, Any]] = []

    def fundamental(self, context: dict[str, Any], *, run_mode: str, use_real_llm: bool) -> dict[str, Any]:
        validate_knowledge_mode(run_mode, LLMKnowledgeMode.STRUCTURED_INPUT_ONLY)
        return self._call(
            context=context,
            task="fundamental_structured_inference",
            prompt_version=FUNDAMENTAL_PROMPT_VERSION,
            schema=FundamentalInference,
            use_real_llm=use_real_llm,
        )

    def screening(self, context: dict[str, Any], *, run_mode: str, use_real_llm: bool) -> dict[str, Any]:
        validate_knowledge_mode(run_mode, LLMKnowledgeMode.STRUCTURED_INPUT_ONLY)
        return self._call(
            context=context,
            task="structured_light_screening",
            prompt_version=SCREENING_PROMPT_VERSION,
            schema=StructuredLightScreening,
            use_real_llm=use_real_llm,
        )

    def _call(self, *, context: dict[str, Any], task: str, prompt_version: str, schema, use_real_llm: bool) -> dict[str, Any]:
        if not use_real_llm:
            return self._dry_result(context, schema)
        self._guard_real_calls()
        request = LLMRequest(
            agent_name="model_validation",
            task=task,
            task_type=task,
            model_alias=MODEL_ALIAS,
            messages=[
                LLMMessage(role="system", content=SYSTEM_PROMPT),
                LLMMessage(role="user", content=json.dumps(context, ensure_ascii=False, default=str)[:24000]),
            ],
            prompt_version=prompt_version,
            response_schema=schema.model_json_schema(),
            json_mode=True,
            thinking_mode="disabled",
            allow_fallback=False,
            metadata={
                "stock_code": context.get("stock_code"),
                "knowledge_mode": LLMKnowledgeMode.STRUCTURED_INPUT_ONLY.value,
                "model_validation": True,
            },
        )
        response = self.gateway.chat(request)
        if response.status != "ok":
            raise ValueError(f"{_error_category(response.status)}:{context.get('stock_code')}:{task}")
        payload = response.structured_output or response.parsed_json or {}
        self._validate_output(payload, context)
        payload["stock_code"] = str(context.get("stock_code"))
        parsed = schema.model_validate(payload)
        self.audit.append(self._audit_row(context, task, prompt_version, response, "PASS"))
        return parsed.model_dump(mode="json")

    def _guard_real_calls(self) -> None:
        if not os.getenv("DEEPSEEK_API_KEY", "").strip():
            raise ValueError("DEEPSEEK_API_KEY_NOT_CONFIGURED")
        if not _flag("LLM_REAL_CALLS_ENABLED"):
            raise ValueError("LLM_REAL_CALLS_DISABLED")
        if not _flag("RUN_REAL_FUNDAMENTAL_RESEARCH"):
            raise ValueError("RUN_REAL_FUNDAMENTAL_RESEARCH_DISABLED")
        if self.gateway.config.mock_only:
            raise ValueError("REAL_LLM_GATEWAY_MOCK_ONLY")
        if self.gateway.usage.budget_exhausted:
            raise ValueError("REAL_LLM_BUDGET_BLOCKED")

    @staticmethod
    def _validate_output(payload: dict[str, Any], context: dict[str, Any]) -> None:
        text = json.dumps(payload, ensure_ascii=False)
        forbidden = (r"https?://", "今日", "目前", "最新", "近期消息", "联网", "全球第一", "国内第一")
        if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in forbidden):
            raise ValueError("STRUCTURED_OUTPUT_CONTAINS_UNBOUNDED_KNOWLEDGE")
        output_code = str(payload.get("stock_code") or "")
        context_code = str(context.get("stock_code") or "")
        if _canonical_stock_code(output_code) != _canonical_stock_code(context_code):
            raise ValueError("STRUCTURED_OUTPUT_STOCK_CODE_MISMATCH")

    @staticmethod
    def _audit_row(context: dict[str, Any], task: str, prompt_version: str, response: LLMResponse, schema_status: str) -> dict[str, Any]:
        return {
            "stock_code": context.get("stock_code"), "task": task,
            "knowledge_mode": LLMKnowledgeMode.STRUCTURED_INPUT_ONLY.value,
            "model_alias": response.model_alias or MODEL_ALIAS, "actual_model": response.model,
            "prompt_version": prompt_version, "status": response.status, "schema_status": schema_status,
            "request_hash": response.request_hash, "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens, "cost_usd": response.cost_usd,
            "latency_ms": response.latency_ms, "cache_status": "HIT" if response.cached else "MISS",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _dry_result(context: dict[str, Any], schema) -> dict[str, Any]:
        code = str(context.get("stock_code") or "UNKNOWN")
        if schema is StructuredLightScreening:
            return StructuredLightScreening(
                stock_code=code, screening_decision="WATCH_ONLY", llm_score=0, confidence=0,
                reason="DRY_RUN_NO_MODEL_CALL", risk_note="仅检查输入与门禁，未调用模型。",
                missing_data=list(context.get("missing_fields") or []),
            ).model_dump(mode="json")
        from research.deepseek_unverified import DeepSeekUnverifiedResearchProvider
        return DeepSeekUnverifiedResearchProvider._dry_run(context).model_dump(mode="json")


def _flag(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on"}


def _canonical_stock_code(value: str) -> str:
    token = value.strip().upper().split(".", maxsplit=1)[0]
    return token.zfill(6) if token.isdigit() and len(token) <= 6 else token


def _error_category(status: str) -> str:
    return {
        "auth_failed": "AUTH_FAILED",
        "insufficient_balance": "INSUFFICIENT_BALANCE",
        "rate_limited": "RATE_LIMITED",
        "provider_error": "PROVIDER_ERROR",
        "model_not_available": "MODEL_NOT_AVAILABLE",
        "schema_error": "SCHEMA_ERROR",
        "budget_exceeded": "BUDGET_EXCEEDED",
        "not_configured": "NOT_CONFIGURED",
    }.get(status, f"LLM_CALL_FAILED:{status}")
