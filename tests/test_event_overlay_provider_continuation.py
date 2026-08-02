from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from event_overlay.checkpoint import CHECKPOINT_CONTRACT_VERSION
from event_overlay.constants import SEARCH_CONTRACT_VERSION
from event_overlay.provider import (
    DeepSeekFlashDirectSearchProvider,
    DirectSearchProviderError,
)
from event_overlay.service import EventOverlayShadowService, REAL_SEARCH_CANARY_MAX


NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone(timedelta(hours=8)))
STOCK = {
    "stock_code": "000001",
    "stock_name": "平安银行",
    "rank": 1,
    "total_score": 80.0,
}
PROMPT = {
    "temperature": 0.1,
    "system": "Return one JSON object.",
    "prompt_version": "TEST_CONTINUATION_V1",
}


def _provider(
    monkeypatch: pytest.MonkeyPatch,
    handler,
    *,
    max_continuations: int = 2,
) -> DeepSeekFlashDirectSearchProvider:
    monkeypatch.setenv("LLM_REAL_CALLS_ENABLED", "true")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret-never-serialize")
    return DeepSeekFlashDirectSearchProvider(
        PROMPT,
        transport=httpx.MockTransport(handler),
        endpoint="https://example.test/anthropic/v1/messages",
        max_uses=2,
        max_continuations=max_continuations,
    )


def _server_tool_blocks(round_number: int) -> list[dict]:
    tool_id = f"server-search-{round_number}"
    return [
        {
            "type": "server_tool_use",
            "id": tool_id,
            "name": "web_search",
            "input": {"query": "平安银行 最新公告"},
        },
        {
            "type": "web_search_tool_result",
            "tool_use_id": tool_id,
            "content": [{"type": "web_search_result", "url": "https://example.com"}],
        },
    ]


def test_server_tool_no_text_continues_with_original_messages_and_counts(monkeypatch):
    requests: list[dict] = []
    first_blocks = _server_tool_blocks(1)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(200, json={
                "model": "deepseek-v4-flash",
                "stop_reason": "tool_use",
                "content": first_blocks,
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "server_tool_use": {"web_search_requests": 1},
                },
            })
        return httpx.Response(200, json={
            "model": "deepseek-v4-flash",
            "stop_reason": "end_turn",
            "content": [{
                "type": "text",
                "text": json.dumps({"events": [{"title": "无新增重大事件"}]}, ensure_ascii=False),
            }],
            "usage": {
                "input_tokens": 12,
                "output_tokens": 8,
                "server_tool_use": {"web_search_requests": 0},
            },
        })

    result = _provider(monkeypatch, handler).search(
        STOCK,
        decision_as_of_time=NOW,
        max_sources=3,
    )

    assert result["network_calls"] == 2
    assert result["usage"]["web_search_requests"] == 1
    assert result["usage"]["server_tool_continuations"] == 1
    assert result["usage"]["input_tokens"] == 22
    assert result["usage"]["output_tokens"] == 13
    assert len(requests) == 2
    assert requests[1]["messages"][0] == requests[0]["messages"][0]
    assert requests[1]["messages"][1] == {
        "role": "assistant",
        "content": first_blocks,
    }
    assert requests[1]["tools"] == requests[0]["tools"]
    assert requests[0]["temperature"] == pytest.approx(0.1)
    assert "test-secret-never-serialize" not in json.dumps(
        {"requests": requests, "result": result},
        ensure_ascii=False,
    )


def test_server_tool_explanatory_text_is_not_parsed_before_continuation(monkeypatch):
    requests: list[dict] = []
    first_blocks = [
        {"type": "text", "text": "Searching the latest public evidence."},
        *_server_tool_blocks(1),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(200, json={
                "model": "deepseek-v4-flash",
                "stop_reason": "tool_use",
                "content": first_blocks,
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "server_tool_use": {"web_search_requests": 1},
                },
            })
        return httpx.Response(200, json={
            "model": "deepseek-v4-flash",
            "stop_reason": "end_turn",
            "content": [{
                "type": "text",
                "text": json.dumps({"events": []}),
            }],
            "usage": {
                "input_tokens": 12,
                "output_tokens": 8,
                "server_tool_use": {"web_search_requests": 0},
            },
        })

    result = _provider(monkeypatch, handler).search(
        STOCK,
        decision_as_of_time=NOW,
        max_sources=3,
    )

    assert result["items"] == []
    assert result["network_calls"] == 2
    assert result["usage"]["web_search_requests"] == 1
    assert result["usage"]["server_tool_continuations"] == 1
    assert requests[1]["messages"][1] == {
        "role": "assistant",
        "content": first_blocks,
    }


def test_invalid_final_json_gets_one_audited_syntactic_repair(monkeypatch):
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content.decode("utf-8")))
        if len(requests) == 1:
            return httpx.Response(200, json={
                "model": "deepseek-v4-flash",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "not valid json"}],
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "server_tool_use": {"web_search_requests": 1},
                },
            })
        return httpx.Response(200, json={
            "model": "deepseek-v4-flash",
            "stop_reason": "end_turn",
            "content": [{
                "type": "text",
                "text": json.dumps({"events": []}),
            }],
            "usage": {
                "input_tokens": 12,
                "output_tokens": 8,
                "server_tool_use": {"web_search_requests": 0},
            },
        })

    result = _provider(monkeypatch, handler).search(
        STOCK,
        decision_as_of_time=NOW,
        max_sources=3,
    )

    assert result["items"] == []
    assert result["network_calls"] == 2
    assert result["usage"]["json_repair_continuations"] == 1
    assert result["usage"]["web_search_requests"] == 1
    assert requests[1]["messages"][-2] == {
        "role": "assistant",
        "content": [{"type": "text", "text": "not valid json"}],
    }
    assert requests[1]["messages"][-1]["role"] == "user"
    assert "valid JSON" in requests[1]["messages"][-1]["content"]


def test_invalid_json_repair_exhaustion_fails_closed_with_all_counts(monkeypatch):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={
            "model": "deepseek-v4-flash",
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "still not json"}],
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "server_tool_use": {
                    "web_search_requests": 1 if calls == 1 else 0,
                },
            },
        })

    with pytest.raises(DirectSearchProviderError) as caught:
        _provider(monkeypatch, handler).search(
            STOCK,
            decision_as_of_time=NOW,
            max_sources=3,
        )

    assert calls == 2
    assert caught.value.network_calls == 2
    assert caught.value.web_search_requests == 1
    assert "DIRECT_SEARCH_WEB_TOOL_INVALID_JSON:end_turn" in str(caught.value)


def test_server_tool_continuation_exhaustion_fails_closed_with_all_counts(monkeypatch):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={
            "model": "deepseek-v4-flash",
            "stop_reason": "tool_use",
            "content": _server_tool_blocks(calls),
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "server_tool_use": {"web_search_requests": 1},
            },
        })

    with pytest.raises(DirectSearchProviderError) as caught:
        _provider(monkeypatch, handler, max_continuations=2).search(
            STOCK,
            decision_as_of_time=NOW,
            max_sources=3,
        )

    assert calls == 3
    assert caught.value.network_calls == 3
    assert caught.value.web_search_requests == 3
    assert "DIRECT_SEARCH_WEB_TOOL_TEXT_MISSING:tool_use" in str(caught.value)
    assert "test-secret-never-serialize" not in str(caught.value)
    assert "NO_RESULT" not in str(caught.value)


def test_client_tool_use_is_not_auto_continued(monkeypatch):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={
            "stop_reason": "tool_use",
            "content": [{
                "type": "tool_use",
                "id": "client-tool-1",
                "name": "local_tool",
                "input": {},
            }],
            "usage": {"server_tool_use": {"web_search_requests": 0}},
        })

    with pytest.raises(DirectSearchProviderError) as caught:
        _provider(monkeypatch, handler).search(
            STOCK,
            decision_as_of_time=NOW,
            max_sources=3,
        )

    assert calls == 1
    assert caught.value.network_calls == 1
    assert "CLIENT_TOOL_USE_REQUIRES_RESULT" in str(caught.value)


def test_canary_accepts_server_tool_continuations_but_rejects_search_failed(tmp_path):
    trade_date = date(2026, 7, 31)
    output = tmp_path / "outputs" / "event_overlay" / trade_date.isoformat()
    run = output / "canary-ok"
    run.mkdir(parents=True)
    manifest = {
        "final_status": "V3_EVENT_OVERLAY_SHADOW_READY",
        "real_search_enabled": True,
        "input_count": REAL_SEARCH_CANARY_MAX,
        "search_failure_count": 0,
        "provider_failure_count": 0,
        "web_search_request_count": 5,
        "actual_network_calls": 7,
        "successful_evaluation_count": REAL_SEARCH_CANARY_MAX,
        "reused_checkpoint_count": 0,
        "source_run_id": "source-run",
        "source_artifact": {"sha256": "a" * 64},
        "universe_source_artifact": {"sha256": "b" * 64},
        "prompt_hash": "c" * 64,
        "search_contract_version": SEARCH_CONTRACT_VERSION,
        "checkpoint_contract_version": CHECKPOINT_CONTRACT_VERSION,
    }
    (run / "run_manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    service = object.__new__(EventOverlayShadowService)
    service.root = Path(tmp_path)
    options = SimpleNamespace(trade_date=trade_date)
    service._assert_successful_canary(
        options,
        source={"run_id": "source-run"},
        source_file_hash="a" * 64,
        universe_file_hash="b" * 64,
        prompt_hash="c" * 64,
    )

    manifest["search_failure_count"] = 1
    manifest["provider_failure_count"] = 1
    (run / "run_manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="SUCCESSFUL_REAL_SEARCH_CANARY_REQUIRED"):
        service._assert_successful_canary(
            options,
            source={"run_id": "source-run"},
            source_file_hash="a" * 64,
            universe_file_hash="b" * 64,
            prompt_hash="c" * 64,
        )
