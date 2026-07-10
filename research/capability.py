from __future__ import annotations

from typing import Any


CAPABILITY_NOT_AVAILABLE = "CAPABILITY_NOT_AVAILABLE_FOR_APPLICATION_API"


class DeepSeekWebCapabilityProbe:
    """Reports the documented application-API capability without fabricating a search.

    DeepSeek's Chat Completion API currently accepts caller-defined function tools.
    A provider-hosted web-search tool with URL-bearing evidence is not documented, so
    making an ordinary model call cannot establish web-search capability.
    """

    def run(self) -> dict[str, Any]:
        return {
            "provider": "deepseek",
            "api_surface": "application_chat_completion",
            "model_alias": "search-analysis-fast",
            "model": "deepseek-v4-flash",
            "tool_or_search_feature": "caller_defined_functions_only",
            "status": CAPABILITY_NOT_AVAILABLE,
            "official_web_search_tool_documented": False,
            "search_executed": False,
            "model_call_executed": False,
            "returned_auditable_urls": False,
            "returned_titles": False,
            "returned_published_at": False,
            "tool_metadata_persistable": False,
            "suitable_for_application_calls": False,
            "claude_code_product_integration_only": False,
            "suitable_for_fundamental_research": False,
            "reason": (
                "The documented application API supports caller-defined function tools, "
                "not a provider-hosted auditable web-search tool."
            ),
        }
