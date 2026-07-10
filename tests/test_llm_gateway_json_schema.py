import json

from llm_gateway.base import BaseLLMProvider
from llm_gateway.json_output import parse_json_object, validate_json_schema
from llm_gateway.registry import LLMProviderRegistry
from llm_gateway.router import LLMRouter, LLMRouterConfig, build_request_hash
from llm_gateway.schemas import LLMMessage, LLMProviderInfo, LLMRequest, LLMResponse


SCHEMA = {
    "type": "object",
    "required": ["status", "confidence"],
    "properties": {
        "status": {"type": "string", "enum": ["OK"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
}


class ScriptedProvider(BaseLLMProvider):
    def __init__(self, contents: list[str]) -> None:
        super().__init__("scripted", True, False)
        self.contents = contents
        self.calls = 0

    def list_models(self):
        return ["scripted-model"]

    def health_check(self):
        return LLMProviderInfo(name=self.name, enabled=True, is_mock=False, models=self.list_models(), status="ok")

    def chat(self, request):
        content = self.contents[self.calls]
        self.calls += 1
        return LLMResponse(
            provider=self.name,
            model="scripted-model",
            content=content,
            input_tokens=2,
            output_tokens=2,
            total_tokens=4,
            latency_ms=1,
            request_hash="",
            status="ok",
        )


def _router(provider: ScriptedProvider) -> LLMRouter:
    registry = LLMProviderRegistry({"scripted": provider})
    return LLMRouter(
        LLMRouterConfig(
            mock_only=False,
            default_provider="scripted",
            default_model="scripted-model",
            cache_enabled=False,
            cache_ttl_seconds=30,
            routing={"default": {"provider": "scripted", "model": "scripted-model"}},
            fallback={},
            budgets={},
        ),
        registry=registry,
    )


def _request(**updates) -> LLMRequest:
    request = LLMRequest(
        agent_name="schema_agent",
        task="schema_test",
        messages=[LLMMessage(role="user", content="json")],
        response_schema=SCHEMA,
        json_mode=True,
        allow_fallback=False,
        prompt_version="v1",
        metadata={"stock_code": "000001"},
    )
    return request.model_copy(update=updates)


def test_markdown_json_fence_is_supported_without_regex_salvage() -> None:
    parsed = parse_json_object('```json\n{"status":"OK","confidence":0.8}\n```')
    validate_json_schema(parsed, SCHEMA)
    assert parsed["status"] == "OK"


def test_invalid_first_output_is_repaired_once() -> None:
    provider = ScriptedProvider(["not-json", json.dumps({"status": "OK", "confidence": 0.8})])
    response = _router(provider).chat(_request())

    assert response.status == "ok"
    assert response.parsed_json == {"status": "OK", "confidence": 0.8}
    assert response.raw_response_metadata["repair_attempted"] is True
    assert response.total_tokens == 8
    assert provider.calls == 2


def test_second_schema_failure_returns_explicit_safe_failure() -> None:
    provider = ScriptedProvider(["not-json", '{"status":"WRONG"}'])
    response = _router(provider).chat(_request())

    assert response.status == "schema_error"
    assert response.content == ""
    assert response.parsed_json is None
    assert provider.calls == 2


def test_request_hash_covers_prompt_version_and_stock_metadata() -> None:
    base = _request()
    assert build_request_hash(base) == build_request_hash(base)
    assert build_request_hash(base) != build_request_hash(_request(prompt_version="v2"))
    assert build_request_hash(base) != build_request_hash(_request(metadata={"stock_code": "000002"}))


def test_request_hash_excludes_sensitive_metadata_values() -> None:
    secret_a = _request(metadata={"stock_code": "000001", "api_key": "secret-a"})
    secret_b = _request(metadata={"stock_code": "000001", "api_key": "secret-b"})
    assert build_request_hash(secret_a) == build_request_hash(secret_b)
