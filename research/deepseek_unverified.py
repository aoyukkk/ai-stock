from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any

from llm_gateway.schemas import LLMMessage, LLMRequest
from llm_gateway.service import LLMGatewayService, get_llm_gateway_service
from research.inference_schemas import FundamentalInference


PROMPT_VERSION = "fundamental_unverified_v1"
SYSTEM_PROMPT = """你没有可审计的联网搜索能力。
你只能依据输入的Tushare结构化资料、系统已经提供的数据以及模型已有知识进行保守推断。
你不得声称已经搜索网络，不得编造URL、公告编号、市场份额、排名、订单金额、客户名称或具体统计数字。
没有外部来源支持的结论必须标记为LLM_UNVERIFIED。强结论证据不足时必须降低结论强度并说明限制。
只输出符合JSON Schema的JSON，不输出BUY/SELL、价格、仓位或reasoning_content。"""


class DeepSeekUnverifiedResearchProvider:
    name = "deepseek_unverified"

    def __init__(self, gateway: LLMGatewayService | None = None) -> None:
        self.gateway = gateway or get_llm_gateway_service()
        self.last_usage: dict[str, Any] = {}

    def infer(self, context: dict[str, Any], *, use_real_llm: bool = False, temporal_manifest=None) -> dict[str, Any]:
        if not use_real_llm:
            return self._dry_run(context).model_dump(mode="json")
        temporal_status = getattr(temporal_manifest, "temporal_status", None)
        temporal_status = getattr(temporal_status, "value", temporal_status)
        if temporal_manifest is None or not temporal_manifest.actionable or temporal_status not in {"PASS", "PASS_WITH_WARNINGS"}:
            raise ValueError("REAL_LLM_TEMPORAL_GATE_BLOCKED")
        if not os.getenv("DEEPSEEK_API_KEY", "").strip() or not _flag("LLM_REAL_CALLS_ENABLED") or not _flag("RUN_REAL_FUNDAMENTAL_RESEARCH"):
            raise ValueError("REAL_LLM_GUARDS_NOT_SATISFIED")
        if self.gateway.config.mock_only:
            raise ValueError("REAL_LLM_GATEWAY_MOCK_ONLY")
        request = LLMRequest(
            agent_name="fundamental_research",
            task="conservative_unverified_fundamental_inference",
            task_type="fundamental_inference",
            model_alias="search-analysis-fast",
            messages=[
                LLMMessage(role="system", content=SYSTEM_PROMPT),
                LLMMessage(role="user", content=json.dumps(context, ensure_ascii=False, default=str)[:16000]),
            ],
            prompt_version=PROMPT_VERSION,
            response_schema=FundamentalInference.model_json_schema(),
            json_mode=True,
            thinking_mode="disabled",
            allow_fallback=False,
            metadata={"stock_code": context.get("stock_code"), "research_mode": "DEEPSEEK_UNVERIFIED", "input_profile_version": context.get("profile_version")},
        )
        response = self.gateway.chat(request)
        payload = response.structured_output or response.parsed_json or {}
        self._validate_no_fabricated_search(payload)
        result = FundamentalInference.model_validate(payload)
        self.last_usage = {
            "model_alias": response.model_alias,
            "actual_model": response.model,
            "request_hash": response.request_hash,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "latency_ms": response.latency_ms,
            "cost_usd": response.cost_usd,
            "prompt_version": PROMPT_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "input_profile_version": context.get("profile_version"),
        }
        return result.model_dump(mode="json")

    @staticmethod
    def _validate_no_fabricated_search(payload: dict[str, Any]) -> None:
        text = json.dumps(payload, ensure_ascii=False)
        forbidden = (r"https?://", "根据刚刚搜索", "通过联网查询", "国内第一", "全球第一", "唯一供应商")
        if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in forbidden):
            raise ValueError("unverified inference contains forbidden search or ranking claim")

    @staticmethod
    def _dry_run(context: dict[str, Any]) -> FundamentalInference:
        stock_code = str(context.get("stock_code") or "UNKNOWN")
        financial = context.get("financial_status") or {"status": "INSUFFICIENT_DATA", "source_status": "DERIVED_RULE"}
        generic = {"summary": "缺少外部证据，等待人工或正式搜索Provider核验。", "confidence": 0.2}
        return FundamentalInference.model_validate({
            "stock_code": stock_code,
            "as_of_time": datetime.now(timezone.utc),
            "industry_chain": {"chain_position": "UNKNOWN", "direct_or_indirect": "UNKNOWN", "confidence": 0.2, "reason": "仅依据结构化主营资料的保守推断。"},
            "level_one_sector_explanation": generic,
            "main_business_summary": generic,
            "industry_position": {"level": "UNCLEAR", "description": "行业地位尚待第三方资料验证。", "confidence": 0.15, "limitations": ["没有市场份额或排名证据"]},
            "main_theme": {"strength": 0, "core_beneficiary": False, "confidence": 0.15},
            "competitive_advantage": generic,
            "industry_trend": generic,
            "investment_logic": generic,
            "domestic_substitution": {"level": "INSUFFICIENT_DATA", "confidence": 0.1},
            "observation_rating": "INSUFFICIENT_DATA",
            "financial_status": financial,
            "missing_fields": list(context.get("missing_fields") or []),
        })


def _flag(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on"}
