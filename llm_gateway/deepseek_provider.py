from __future__ import annotations

import os
import time
from typing import Any, Callable

import httpx

from backend.core.security import redact_sensitive_text
from llm_gateway.base import BaseLLMProvider
from llm_gateway.exceptions import (
    LLMAuthenticationError,
    LLMInsufficientBalanceError,
    LLMProviderNotConfiguredError,
    LLMProviderResponseError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from llm_gateway.json_output import parse_json_object
from llm_gateway.schemas import LLMProviderInfo, LLMRequest, LLMResponse


class DeepSeekLLMProvider(BaseLLMProvider):
    """Minimal OpenAI-compatible DeepSeek HTTP provider behind the gateway."""

    def __init__(
        self,
        *,
        enabled: bool,
        api_key_env: str,
        models: list[str],
        base_url: str = "https://api.deepseek.com",
        connect_timeout_seconds: float = 5.0,
        read_timeout_seconds: float = 60.0,
        max_attempts: int = 3,
        retry_backoff_seconds: float = 0.5,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(name="deepseek", enabled=enabled, is_mock=False)
        self.api_key_env = api_key_env
        self._models = list(models)
        self.base_url = base_url.rstrip("/")
        self.connect_timeout_seconds = connect_timeout_seconds
        self.read_timeout_seconds = read_timeout_seconds
        self.max_attempts = max(1, max_attempts)
        self.retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        self.transport = transport
        self.sleep = sleep

    @property
    def configured(self) -> bool:
        return bool(os.environ.get(self.api_key_env, "").strip())

    def list_models(self) -> list[str]:
        return list(self._models)

    def fetch_available_models(self) -> list[str]:
        api_key = self._api_key()
        timeout = self._timeout()
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                with httpx.Client(timeout=timeout, transport=self.transport) as client:
                    response = client.get(
                        f"{self.base_url}/models",
                        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
                    )
                if response.status_code in {401, 403}:
                    raise LLMAuthenticationError("DeepSeek authentication failed.")
                if response.status_code == 402:
                    raise LLMInsufficientBalanceError("DeepSeek account has insufficient balance.")
                if response.status_code == 429:
                    last_error = LLMRateLimitError("DeepSeek rate limit reached.")
                elif 500 <= response.status_code < 600:
                    last_error = LLMProviderResponseError(f"DeepSeek provider returned HTTP {response.status_code}.")
                elif response.status_code >= 400:
                    raise LLMProviderResponseError(f"DeepSeek provider returned HTTP {response.status_code}.")
                else:
                    data = response.json()
                    items = data.get("data") if isinstance(data, dict) else None
                    if not isinstance(items, list):
                        raise LLMProviderResponseError("DeepSeek returned a malformed model list.")
                    return sorted({str(item.get("id")) for item in items if isinstance(item, dict) and item.get("id")})
                if attempt < self.max_attempts:
                    self._backoff(attempt)
                    continue
                raise last_error
            except (LLMAuthenticationError, LLMInsufficientBalanceError):
                raise
            except httpx.TimeoutException as exc:
                last_error = LLMTimeoutError("DeepSeek model-list request timed out.")
                if attempt < self.max_attempts:
                    self._backoff(attempt)
                    continue
                raise last_error from exc
            except httpx.NetworkError as exc:
                last_error = LLMProviderResponseError("DeepSeek model-list network request failed.")
                if attempt < self.max_attempts:
                    self._backoff(attempt)
                    continue
                raise last_error from exc
        raise LLMProviderResponseError(redact_sensitive_text(last_error or "DeepSeek model-list request failed."))

    def health_check(self) -> LLMProviderInfo:
        if not self.enabled:
            status, message = "disabled", "Provider is disabled by configuration."
        elif not self.configured:
            status, message = "not_configured", "Provider configuration is incomplete."
        else:
            status, message = "configured", "Provider is configured; no network request was made."
        return LLMProviderInfo(
            name=self.name,
            enabled=self.enabled,
            is_mock=False,
            models=self.list_models(),
            status=status,
            message=message,
        )

    def chat(self, request: LLMRequest) -> LLMResponse:
        if not self.enabled:
            raise LLMProviderNotConfiguredError("DeepSeek provider is disabled by configuration.")
        api_key = self._api_key()

        payload: dict[str, Any] = {
            "model": request.model,
            "messages": [message.model_dump(mode="json") for message in request.messages],
            "stream": False,
        }
        if request.thinking_mode is not None:
            payload["thinking"] = {"type": request.thinking_mode}
        if request.reasoning_effort is not None:
            payload["reasoning_effort"] = request.reasoning_effort
        if request.temperature is not None and request.thinking_mode != "enabled":
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.json_mode or request.response_schema is not None or request.metadata.get("structured"):
            payload["response_format"] = {"type": "json_object"}

        timeout = self._timeout()
        started = time.perf_counter()
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                with httpx.Client(timeout=timeout, transport=self.transport) as client:
                    http_response = client.post(
                        f"{self.base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                        json=payload,
                    )
                if http_response.status_code in {401, 403}:
                    raise LLMAuthenticationError("DeepSeek authentication failed.")
                if http_response.status_code == 402:
                    raise LLMInsufficientBalanceError("DeepSeek account has insufficient balance.")
                if http_response.status_code == 429:
                    last_error = LLMRateLimitError("DeepSeek rate limit reached.")
                    if attempt < self.max_attempts:
                        self._backoff(attempt)
                        continue
                    raise last_error
                if 500 <= http_response.status_code < 600:
                    last_error = LLMProviderResponseError(f"DeepSeek provider returned HTTP {http_response.status_code}.")
                    if attempt < self.max_attempts:
                        self._backoff(attempt)
                        continue
                    raise last_error
                if http_response.status_code >= 400:
                    raise LLMProviderResponseError(f"DeepSeek provider returned HTTP {http_response.status_code}.")
                return self._normalize_response(http_response, request, started)
            except (LLMAuthenticationError, LLMInsufficientBalanceError):
                raise
            except (httpx.TimeoutException,) as exc:
                last_error = LLMTimeoutError("DeepSeek request timed out.")
                if attempt < self.max_attempts:
                    self._backoff(attempt)
                    continue
                raise last_error from exc
            except httpx.NetworkError as exc:
                last_error = LLMProviderResponseError("DeepSeek network request failed.")
                if attempt < self.max_attempts:
                    self._backoff(attempt)
                    continue
                raise last_error from exc
            except (LLMRateLimitError, LLMProviderResponseError):
                raise
            except Exception as exc:
                raise LLMProviderResponseError(redact_sensitive_text(exc)) from exc
        raise LLMProviderResponseError(redact_sensitive_text(last_error or "DeepSeek request failed."))

    def _normalize_response(self, response: httpx.Response, request: LLMRequest, started: float) -> LLMResponse:
        try:
            data = response.json()
            choice = data["choices"][0]
            content = choice["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("message content is not text")
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMProviderResponseError("DeepSeek returned a malformed response.") from exc

        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        input_tokens = int(usage.get("prompt_tokens") or 0)
        cache_hit_tokens = int(usage.get("prompt_cache_hit_tokens") or 0)
        cache_miss_tokens = int(usage.get("prompt_cache_miss_tokens") or max(0, input_tokens - cache_hit_tokens))
        output_tokens = int(usage.get("completion_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or input_tokens + output_tokens)
        parsed_json = None
        if request.json_mode or request.response_schema is not None or request.metadata.get("structured"):
            try:
                parsed_json = parse_json_object(content)
            except Exception:
                parsed_json = None

        return LLMResponse(
            provider=self.name,
            model=str(data.get("model") or request.model or ""),
            content=content,
            parsed_json=parsed_json,
            structured_output=parsed_json,
            input_tokens=input_tokens,
            input_cache_hit_tokens=cache_hit_tokens,
            input_cache_miss_tokens=cache_miss_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost_usd=None,
            latency_ms=int((time.perf_counter() - started) * 1000),
            request_hash="",
            prompt_version=request.prompt_version,
            model_version=str(data.get("model") or request.model or ""),
            status="ok",
            finish_reason=choice.get("finish_reason"),
            raw_response_metadata={
                "response_id": data.get("id"),
                "system_fingerprint": data.get("system_fingerprint"),
                "http_status": response.status_code,
                "reasoning_tokens": int(
                    (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
                ) if isinstance(usage.get("completion_tokens_details"), dict) else 0,
            },
        )

    def _api_key(self) -> str:
        if not self.enabled:
            raise LLMProviderNotConfiguredError("DeepSeek provider is disabled by configuration.")
        api_key = os.environ.get(self.api_key_env, "").strip()
        if not api_key:
            raise LLMProviderNotConfiguredError("DeepSeek provider credential is not configured.")
        return api_key

    def _timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self.connect_timeout_seconds,
            read=self.read_timeout_seconds,
            write=self.connect_timeout_seconds,
            pool=self.connect_timeout_seconds,
        )

    def _backoff(self, attempt: int) -> None:
        self.sleep(self.retry_backoff_seconds * (2 ** (attempt - 1)))
