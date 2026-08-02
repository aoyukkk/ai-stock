from __future__ import annotations

import json
import math
import os
import base64
import binascii
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Protocol

import httpx

from backend.core.security import redact_sensitive_text
from llm_gateway.json_output import parse_json_object
from llm_gateway.schemas import LLMMessage, LLMRequest

from event_overlay.constants import DIRECT_SEARCH_FALLBACK, SEARCH_CONTRACT_VERSION


class EventSearchProvider(Protocol):
    name: str

    def search(self, stock: dict[str, Any], *, decision_as_of_time: datetime, max_sources: int) -> dict[str, Any]: ...


class DirectSearchProviderError(RuntimeError):
    """Fail-closed direct-search error with redacted usage accounting.

    A provider may have completed one or more HTTP rounds before the server-tool
    exchange fails.  The service must retain those calls in the run manifest
    even though no search result is eligible for use.
    """

    def __init__(
        self,
        message: str,
        *,
        network_calls: int = 0,
        web_search_requests: int = 0,
    ) -> None:
        super().__init__(redact_sensitive_text(message)[:240])
        self.network_calls = max(0, int(network_calls))
        self.web_search_requests = max(0, int(web_search_requests))


class DirectSearchTimeoutError(DirectSearchProviderError):
    pass


class MockEventSearchProvider:
    """Deterministic provider used by all tests and the mandatory mock canary."""

    name = "mock_event_search"

    def search(self, stock: dict[str, Any], *, decision_as_of_time: datetime, max_sources: int) -> dict[str, Any]:
        code = str(stock["stock_code"])
        seed = int(sha256(code.encode("utf-8")).hexdigest()[:8], 16)
        direction = ("POSITIVE", "NEUTRAL", "NEGATIVE")[seed % 3]
        event_type = ("ORDER", "POLICY", "EARNINGS", "INDUSTRY")[seed % 4]
        url = f"https://example.com/event/{code}/{decision_as_of_time.date().isoformat()}"
        return {
            "status": "VERIFIED_WITH_STRUCTURED_PROVIDER",
            "provider": self.name,
            "provider_verified": True,
            "direct_search_used": False,
            "items": [{
                "event_type": event_type,
                "title": f"{stock.get('stock_name') or code} 模拟事件证据",
                "summary": "仅用于离线合同与端到端测试，不代表真实新闻。",
                "url": url,
                "published_at": decision_as_of_time.isoformat(),
                "source_tier": "tier_2",
                "source_type": "MOCK_TEST_EVIDENCE",
                "event_direction": direction,
                "materiality": 0.4 + (seed % 5) / 10,
                "relevance": 0.8,
                "confidence": 0.8,
            }][:max_sources],
            "network_calls": 0,
        }


class NoNetworkEventSearchProvider:
    name = "no_network"

    def __init__(self, *, historical: bool = False) -> None:
        self.historical = historical

    def search(self, stock: dict[str, Any], *, decision_as_of_time: datetime, max_sources: int) -> dict[str, Any]:
        return {
            "status": "HISTORICAL_EVIDENCE_UNAVAILABLE" if self.historical else "NO_RESULT_FOUND",
            "provider": self.name,
            "provider_verified": False,
            "direct_search_used": False,
            "items": [],
            "network_calls": 0,
        }


class DeepSeekFlashDirectSearchProvider:
    """Explicit Shadow-only DeepSeek v4 Flash direct-search fallback.

    The Anthropic-compatible route exposes auditable server-side web-search
    usage. Individual source claims remain Shadow-only and receive the
    configured confidence discount.
    """

    name = "deepseek_v4_flash_direct_search"

    def __init__(
        self,
        prompt: dict[str, Any],
        gateway=None,
        *,
        transport: httpx.BaseTransport | None = None,
        endpoint: str | None = None,
        model: str | None = None,
        max_uses: int = 3,
        max_tokens: int = 6000,
        max_continuations: int = 2,
    ) -> None:
        self.prompt = prompt
        self.gateway = gateway
        self.transport = transport
        self.endpoint = endpoint or "https://api.deepseek.com/anthropic/v1/messages"
        self.model = model or "deepseek-v4-flash"
        self.max_uses = max(1, int(max_uses))
        self.max_tokens = max(1024, int(max_tokens))
        self.max_continuations = max(0, min(4, int(max_continuations)))

    def search(self, stock: dict[str, Any], *, decision_as_of_time: datetime, max_sources: int) -> dict[str, Any]:
        if not _flag("LLM_REAL_CALLS_ENABLED") or not os.getenv("DEEPSEEK_API_KEY", "").strip():
            raise RuntimeError("DIRECT_SEARCH_REAL_CALL_GUARDS_NOT_SATISFIED")
        if self.gateway is None:
            return self._search_with_anthropic_web_tool(
                stock,
                decision_as_of_time=decision_as_of_time,
                max_sources=max_sources,
            )
        payload = {
            "stock": stock,
            "decision_as_of_time": decision_as_of_time.isoformat(),
            "max_sources": max_sources,
            "contract": SEARCH_CONTRACT_VERSION,
            "output_format": "json_object",
            "warning": "URLs and publish times must be returned when available; unknown values must be null.",
        }
        request = LLMRequest(
            agent_name="event_overlay_shadow",
            task="v3_event_overlay_direct_search",
            task_type="search_analysis",
            model_alias="search-analysis-fast",
            temperature=float(self.prompt.get("temperature", 0.1)),
            messages=[
                LLMMessage(role="system", content=str(self.prompt["system"])),
                LLMMessage(role="user", content=json.dumps(payload, ensure_ascii=False, default=str)),
            ],
            max_tokens=4000,
            prompt_version=str(self.prompt["prompt_version"]),
            json_mode=True,
            allow_fallback=False,
            thinking_mode="disabled",
            metadata={
                "search_provider_enabled": True,
                # The global gateway is deliberately mock-first.  A real V3
                # canary already passed the explicit CLI, env and size gates,
                # so carry that intent into the router instead of silently
                # falling back to the mock provider.
                "explicit_real_llm_test": True,
                "production_or_shadow": "SHADOW",
                "direct_search_contract": SEARCH_CONTRACT_VERSION,
            },
        )
        response = self.gateway.chat(request)
        if response.status != "ok":
            error_code = str(response.error or "UNKNOWN")[:240]
            raise RuntimeError(
                f"DIRECT_SEARCH_GATEWAY_ERROR:{response.status}:{error_code}"
            )
        parsed = response.structured_output or response.parsed_json
        if not isinstance(parsed, dict):
            try:
                parsed = json.loads(response.content)
            except (TypeError, json.JSONDecodeError) as exc:
                raise RuntimeError("DIRECT_SEARCH_INVALID_JSON") from exc
        event_rows = (
            parsed.get("material_events")
            or parsed.get("events")
            or parsed.get("items")
            or []
        )
        if not isinstance(event_rows, list):
            raise RuntimeError("DIRECT_SEARCH_EVENTS_NOT_A_LIST")
        return {
            "status": DIRECT_SEARCH_FALLBACK,
            "provider": self.name,
            "provider_verified": False,
            "direct_search_used": True,
            "items": event_rows[:max_sources],
            "network_calls": 1,
            "usage": {
                "model": response.model,
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "latency_ms": response.latency_ms,
                "request_hash": response.request_hash,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
            },
        }

    def _search_with_anthropic_web_tool(
        self,
        stock: dict[str, Any],
        *,
        decision_as_of_time: datetime,
        max_sources: int,
    ) -> dict[str, Any]:
        api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        requested = {
            "stock_code": str(stock["stock_code"]),
            "stock_name": str(stock.get("stock_name") or ""),
            "decision_as_of_time": decision_as_of_time.isoformat(),
            "max_sources": max_sources,
            "contract": SEARCH_CONTRACT_VERSION,
            "required_event_fields": [
                "event_type", "title", "summary", "url", "published_at",
                "source_type", "event_direction",
                "materiality", "relevance", "confidence",
            ],
            "output_format": {"events": "array"},
        }
        system = str(self.prompt["system"])
        messages: list[dict[str, Any]] = [{
            "role": "user",
            "content": json.dumps(requested, ensure_ascii=False, default=str),
        }]
        tools = [{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": self.max_uses,
        }]
        body = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": float(self.prompt.get("temperature", 0.1)),
            "thinking": {
                "type": str(
                    self.prompt.get("thinking_mode") or "disabled"
                )
            },
            "system": system,
            "messages": messages,
            "tools": tools,
        }
        headers = {
            "authorization": f"Bearer {api_key}",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        started = datetime.now(timezone.utc)
        network_calls = 0
        web_search_requests = 0
        input_tokens = 0
        output_tokens = 0
        continuation_count = 0
        json_repair_count = 0
        payload: dict[str, Any] = {}
        blocks: list[Any] = []
        parsed: dict[str, Any] | None = None
        try:
            with httpx.Client(
                timeout=httpx.Timeout(150.0, connect=10.0),
                transport=self.transport,
            ) as client:
                while True:
                    network_calls += 1
                    try:
                        response = client.post(
                            self.endpoint,
                            headers=headers,
                            json=body,
                        )
                    except httpx.TimeoutException as exc:
                        raise DirectSearchTimeoutError(
                            "DIRECT_SEARCH_WEB_TOOL_TIMEOUT",
                            network_calls=network_calls,
                            web_search_requests=web_search_requests,
                        ) from exc
                    except httpx.NetworkError as exc:
                        raise DirectSearchProviderError(
                            "DIRECT_SEARCH_WEB_TOOL_NETWORK_ERROR",
                            network_calls=network_calls,
                            web_search_requests=web_search_requests,
                        ) from exc
                    if response.status_code >= 400:
                        detail = ""
                        try:
                            error = response.json().get("error") or {}
                            if isinstance(error, dict):
                                detail = str(
                                    error.get("type")
                                    or error.get("message")
                                    or ""
                                )
                        except (ValueError, AttributeError, TypeError):
                            detail = ""
                        safe = redact_sensitive_text(detail)[:160]
                        raise DirectSearchProviderError(
                            f"DIRECT_SEARCH_WEB_TOOL_HTTP_{response.status_code}:{safe}",
                            network_calls=network_calls,
                            web_search_requests=web_search_requests,
                        )
                    try:
                        raw_payload = response.json()
                    except ValueError as exc:
                        raise DirectSearchProviderError(
                            "DIRECT_SEARCH_WEB_TOOL_INVALID_RESPONSE",
                            network_calls=network_calls,
                            web_search_requests=web_search_requests,
                        ) from exc
                    if not isinstance(raw_payload, dict):
                        raise DirectSearchProviderError(
                            "DIRECT_SEARCH_WEB_TOOL_INVALID_RESPONSE",
                            network_calls=network_calls,
                            web_search_requests=web_search_requests,
                        )
                    payload = raw_payload
                    raw_blocks = payload.get("content")
                    if not isinstance(raw_blocks, list):
                        raise DirectSearchProviderError(
                            "DIRECT_SEARCH_WEB_TOOL_CONTENT_MISSING",
                            network_calls=network_calls,
                            web_search_requests=web_search_requests,
                        )
                    blocks = raw_blocks
                    usage = payload.get("usage") or {}
                    if isinstance(usage, dict):
                        input_tokens += int(usage.get("input_tokens") or 0)
                        output_tokens += int(usage.get("output_tokens") or 0)
                        server_usage = usage.get("server_tool_use") or {}
                        if isinstance(server_usage, dict):
                            web_search_requests += int(
                                server_usage.get("web_search_requests") or 0
                            )
                    text = "\n".join(
                        str(block.get("text") or "")
                        for block in blocks
                        if isinstance(block, dict)
                        and block.get("type") == "text"
                    ).strip()
                    block_types = {
                        str(block.get("type") or "")
                        for block in blocks
                        if isinstance(block, dict)
                    }
                    if "tool_use" in block_types:
                        raise DirectSearchProviderError(
                            "DIRECT_SEARCH_CLIENT_TOOL_USE_REQUIRES_RESULT",
                            network_calls=network_calls,
                            web_search_requests=web_search_requests,
                        )
                    server_tool_exchange = bool(
                        block_types
                        & {"server_tool_use", "web_search_tool_result"}
                    )
                    continuation_allowed = (
                        server_tool_exchange
                        and str(payload.get("stop_reason") or "")
                        in {"tool_use", "pause_turn"}
                        and continuation_count < self.max_continuations
                    )
                    if continuation_allowed:
                        # Intermediate server-tool turns may contain
                        # explanatory text rather than the final JSON.
                        messages.append({
                            "role": "assistant",
                            "content": blocks,
                        })
                        continuation_count += 1
                        continue
                    if text:
                        try:
                            parsed = _parse_json_payload(text)
                        except ValueError as exc:
                            if json_repair_count == 0:
                                # Keep the evidence-bearing response in the
                                # same conversation and request one strictly
                                # syntactic repair.  This does not turn a
                                # provider failure into "no news", and the
                                # extra HTTP round remains visible in usage.
                                messages.extend([
                                    {
                                        "role": "assistant",
                                        "content": blocks,
                                    },
                                    {
                                        "role": "user",
                                        "content": (
                                            "The preceding answer was not valid JSON. "
                                            "Return only one valid JSON object with an "
                                            "events array. Preserve only the evidence "
                                            "already found; do not invent, broaden, or "
                                            "replace sources. Use events=[] when no "
                                            "qualifying evidence exists."
                                        ),
                                    },
                                ])
                                json_repair_count += 1
                                continue
                            raise DirectSearchProviderError(
                                f"DIRECT_SEARCH_WEB_TOOL_INVALID_JSON:{payload.get('stop_reason')}",
                                network_calls=network_calls,
                                web_search_requests=web_search_requests,
                            ) from exc
                        break
                    raise DirectSearchProviderError(
                        f"DIRECT_SEARCH_WEB_TOOL_TEXT_MISSING:{payload.get('stop_reason')}",
                        network_calls=network_calls,
                        web_search_requests=web_search_requests,
                    )
        except DirectSearchProviderError:
            raise

        if web_search_requests <= 0:
            raise DirectSearchProviderError(
                "DIRECT_SEARCH_WEB_TOOL_NOT_USED",
                network_calls=network_calls,
                web_search_requests=web_search_requests,
            )
        if parsed is None:
            raise DirectSearchProviderError(
                f"DIRECT_SEARCH_WEB_TOOL_TEXT_MISSING:{payload.get('stop_reason')}",
                network_calls=network_calls,
                web_search_requests=web_search_requests,
            )
        event_rows = (
            parsed.get("material_events")
            or parsed.get("events")
            or parsed.get("items")
            or []
        )
        if not isinstance(event_rows, list):
            raise DirectSearchProviderError(
                "DIRECT_SEARCH_EVENTS_NOT_A_LIST",
                network_calls=network_calls,
                web_search_requests=web_search_requests,
            )
        event_rows = _sanitize_event_rows(event_rows)
        return {
            "status": DIRECT_SEARCH_FALLBACK,
            "provider": self.name,
            "provider_verified": False,
            "direct_search_used": True,
            "stock_name": _decode_utf8_base64(parsed.get("stock_name_b64")),
            "items": event_rows[:max_sources],
            "network_calls": network_calls,
            "usage": {
                "model": str(payload.get("model") or self.model),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "latency_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
                "request_hash": sha256(
                    json.dumps(requested, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest(),
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "web_search_requests": web_search_requests,
                "stop_reason": payload.get("stop_reason"),
                "server_tool_continuations": continuation_count,
                "json_repair_continuations": json_repair_count,
                "content_block_types": [
                    block.get("type") for block in blocks if isinstance(block, dict)
                ],
            },
        }

def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_json_payload(text: str) -> dict[str, Any]:
    try:
        return parse_json_object(text)
    except Exception as original:
        decoder = json.JSONDecoder()
        for index, char in enumerate(text):
            if char != "{":
                continue
            try:
                value, _ = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
        raise ValueError("Provider output does not contain a JSON object") from original


def _sanitize_event_rows(rows: list[Any]) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        clean = dict(row)
        decoded_title = _decode_utf8_base64(clean.get("title_b64"))
        decoded_summary = _decode_utf8_base64(clean.get("summary_b64"))
        if decoded_title:
            clean["title"] = decoded_title
        if decoded_summary:
            clean["summary"] = decoded_summary
        clean["title"] = str(clean.get("title") or "未命名事件")[:500]
        clean["summary"] = str(clean.get("summary") or "未提供摘要")[:2000]
        invalid_numeric_fields: list[str] = []
        for field, default in (
            ("materiality", 0.3),
            ("relevance", 0.5),
            ("confidence", 0.3),
        ):
            if not _is_finite_unit_input(clean.get(field)):
                invalid_numeric_fields.append(field)
            clean[field] = _unit_number(clean.get(field), default)
        for field in (
            "impact_magnitude", "directness", "exposure_estimate",
            "exposure_confidence", "price_already_reacted",
            "a_share_breadth_confirmation",
        ):
            if clean.get(field) is not None:
                if not _is_finite_unit_input(clean.get(field)):
                    invalid_numeric_fields.append(field)
                clean[field] = _unit_number(clean.get(field), None)
        if invalid_numeric_fields:
            clean["_invalid_numeric_fields"] = sorted(
                set(invalid_numeric_fields)
            )
        if not isinstance(clean.get("named_company"), bool):
            clean["named_company"] = None
        if clean.get("source_tier") not in {
            "tier_1", "tier_2", "tier_3", "tier_4"
        }:
            # Absence is different from an LLM claim of tier_4.  Evidence
            # normalization derives the effective tier from the URL domain.
            clean.pop("source_tier", None)
        clean["event_direction"] = str(
            clean.get("event_direction") or "NEUTRAL"
        ).upper()
        if clean["event_direction"] not in {
            "POSITIVE", "NEGATIVE", "NEUTRAL", "MIXED"
        }:
            clean["event_direction"] = "NEUTRAL"
        sanitized.append(clean)
    return sanitized


def _decode_utf8_base64(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        decoded = base64.b64decode(raw, validate=True).decode("utf-8").strip()
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    return decoded or None


def _unit_number(value: Any, default: float | None) -> float | None:
    try:
        if isinstance(value, str):
            raw = value.strip()
            if raw.endswith("%"):
                number = float(raw[:-1]) / 100
            else:
                number = float(raw)
        else:
            number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        return default
    return number


def _is_finite_unit_input(value: Any) -> bool:
    try:
        if isinstance(value, str):
            raw = value.strip()
            number = (
                float(raw[:-1]) / 100
                if raw.endswith("%")
                else float(raw)
            )
        else:
            number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and 0.0 <= number <= 1.0
