from __future__ import annotations

from llm_gateway.base import BaseLLMProvider
from llm_gateway.exceptions import PROVIDER_DISABLED_MESSAGE, LLMProviderUnavailableError
from llm_gateway.schemas import LLMProviderInfo, LLMRequest, LLMResponse


class PlaceholderLLMProvider(BaseLLMProvider):
    def __init__(self, name: str, models: list[str] | None = None) -> None:
        super().__init__(name=name, enabled=False, is_mock=False)
        self._models = models or []

    def list_models(self) -> list[str]:
        return list(self._models)

    def health_check(self) -> LLMProviderInfo:
        return LLMProviderInfo(
            name=self.name,
            enabled=False,
            is_mock=False,
            models=self.list_models(),
            status="not_implemented",
            message=PROVIDER_DISABLED_MESSAGE,
        )

    def chat(self, request: LLMRequest) -> LLMResponse:
        raise LLMProviderUnavailableError(PROVIDER_DISABLED_MESSAGE)
