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
    messages: list[LLMMessage]
    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    prompt_version: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class LLMResponse(LLMModel):
    provider: str
    model: str
    content: str
    structured_output: dict[str, Any] | None = None
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cached: bool = False
    cost_usd: float
    latency_ms: int
    request_hash: str
    prompt_version: str | None = None
    model_version: str | None = None
    status: str


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
