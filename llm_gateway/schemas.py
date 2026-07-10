from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class LLMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class LLMMessage(LLMModel):
    role: Literal["system", "user", "assistant"]
    content: str


class LLMRequest(LLMModel):
    agent_name: str
    task: str
    task_type: str | None = None
    task_tier: str | None = None
    messages: list[LLMMessage]
    model_alias: str | None = None
    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    prompt_version: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    response_schema: dict[str, Any] | None = None
    json_mode: bool = False
    allow_fallback: bool = True
    thinking_mode: Literal["enabled", "disabled"] | None = None
    reasoning_effort: Literal["high", "max"] | None = None


class LLMResponse(LLMModel):
    provider: str
    model: str
    model_alias: str | None = None
    task_type: str | None = None
    task_tier: str | None = None
    thinking_mode: str | None = None
    reasoning_effort: str | None = None
    content: str
    parsed_json: dict[str, Any] | None = None
    structured_output: dict[str, Any] | None = None
    input_tokens: int
    input_cache_hit_tokens: int = 0
    input_cache_miss_tokens: int = 0
    output_tokens: int
    total_tokens: int
    cached: bool = False
    cost_usd: float | None = None
    cost_status: str = "COST_NOT_CONFIGURED"
    pricing_version: str | None = None
    latency_ms: int
    request_hash: str
    prompt_version: str | None = None
    model_version: str | None = None
    status: str
    error: str | None = None
    finish_reason: str | None = None
    raw_response_metadata: dict[str, Any] = Field(default_factory=dict)
    usage_estimated: bool = False
    usage_id: int | None = None


class LLMProviderInfo(LLMModel):
    name: str
    enabled: bool
    is_mock: bool
    models: list[str]
    status: str
    message: str | None = None


class PromptTemplate(LLMModel):
    agent_name: str
    version: str
    template: str
    description: str = ""
    is_active: bool = True
    content_hash: str
