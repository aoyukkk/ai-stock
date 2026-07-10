from __future__ import annotations

from typing import Any

from llm_gateway.schemas import LLMMessage, LLMRequest, LLMResponse
from llm_gateway.service import LLMGatewayService


CONNECTIVITY_RESPONSE_SCHEMA = {
    "type": "object",
    "required": ["gateway_check", "schema_version"],
    "properties": {
        "gateway_check": {"type": "string", "enum": ["OK"]},
        "schema_version": {"type": "integer", "enum": [1]},
    },
}

DEEPSEEK_CANARY_RESPONSE_SCHEMA = {
    "type": "object",
    "required": ["status", "provider", "schema_version"],
    "properties": {
        "status": {"type": "string", "enum": ["TEST_OK"]},
        "provider": {"type": "string", "enum": ["deepseek"]},
        "schema_version": {"type": "string"},
    },
}

STATUS_LABELS = {
    "ok": "SUCCESS",
    "not_configured": "NOT_CONFIGURED",
    "auth_failed": "AUTH_FAILED",
    "timeout": "TIMEOUT",
    "rate_limited": "RATE_LIMITED",
    "provider_error": "PROVIDER_ERROR",
    "schema_error": "SCHEMA_ERROR",
    "budget_exceeded": "BUDGET_EXCEEDED",
    "insufficient_balance": "INSUFFICIENT_BALANCE",
}


def run_connectivity_test(service: LLMGatewayService, model_alias: str) -> dict[str, Any]:
    response = service.chat(
        LLMRequest(
            agent_name="gateway_connectivity_test",
            task="connectivity_test",
            model_alias=model_alias,
            messages=[
                LLMMessage(
                    role="system",
                    content="Return JSON only. This is a connectivity test, not financial analysis or a trading recommendation.",
                ),
                LLMMessage(
                    role="user",
                    content='Return exactly this JSON object: {"gateway_check":"OK","schema_version":1}',
                ),
            ],
            temperature=0,
            max_tokens=64,
            prompt_version="gateway-connectivity-v1",
            metadata={"structured": True, "explicit_real_llm_test": True},
            response_schema=CONNECTIVITY_RESPONSE_SCHEMA,
            json_mode=True,
            allow_fallback=False,
        )
    )
    return connectivity_result(response, model_alias)


def run_deepseek_json_canary(service: LLMGatewayService, model_alias: str) -> dict[str, Any]:
    response = service.chat(
        LLMRequest(
            agent_name="gateway_connectivity_test",
            task="connectivity_test",
            model_alias=model_alias,
            messages=[
                LLMMessage(
                    role="system",
                    content="Return JSON only. This is a DeepSeek connectivity canary, not financial analysis or a trading recommendation. Do not include stock data.",
                ),
                LLMMessage(
                    role="user",
                    content='Return exactly this JSON object: {"status":"TEST_OK","provider":"deepseek","schema_version":"connectivity-v1"}',
                ),
            ],
            temperature=0,
            max_tokens=64,
            prompt_version="deepseek-connectivity-canary-v1",
            metadata={"structured": True, "explicit_real_llm_test": True},
            response_schema=DEEPSEEK_CANARY_RESPONSE_SCHEMA,
            json_mode=True,
            allow_fallback=False,
            thinking_mode="disabled",
        )
    )
    result = connectivity_result(response, model_alias)
    result["schema_status"] = "PASS" if result["schema_passed"] else "FAIL"
    return result


def connectivity_result(response: LLMResponse, model_alias: str) -> dict[str, Any]:
    status = STATUS_LABELS.get(response.status, "PROVIDER_ERROR")
    return {
        "provider": response.provider,
        "model_alias": model_alias,
        "model": response.model,
        "status": status,
        "latency_ms": response.latency_ms,
        "token_usage": {
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "total_tokens": response.total_tokens,
        },
        "schema_passed": response.status == "ok" and response.parsed_json is not None,
        "error_category": None if response.status == "ok" else status,
    }
