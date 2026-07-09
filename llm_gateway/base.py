from __future__ import annotations

from abc import ABC, abstractmethod

from llm_gateway.schemas import LLMProviderInfo, LLMRequest, LLMResponse


class BaseLLMProvider(ABC):
    def __init__(self, name: str, enabled: bool, is_mock: bool) -> None:
        self.name = name
        self.enabled = enabled
        self.is_mock = is_mock

    @abstractmethod
    def list_models(self) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def health_check(self) -> LLMProviderInfo:
        raise NotImplementedError

    @abstractmethod
    def chat(self, request: LLMRequest) -> LLMResponse:
        raise NotImplementedError
