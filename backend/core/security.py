from __future__ import annotations

from typing import Any


SENSITIVE_KEYWORDS = (
    "api_key",
    "secret",
    "password",
    "username",
    "credential",
    "key",
)

SAFE_TOKEN_KEYS = {
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "total_tokens",
    "daily_token_budget",
}


def is_sensitive_key(key: str) -> bool:
    normalized = key.lower()
    if normalized in SAFE_TOKEN_KEYS or normalized.endswith("_tokens"):
        return False
    if "token" in normalized and not (
        normalized.endswith("_token_budget") or normalized.endswith("_tokens")
    ):
        return True
    return any(keyword in normalized for keyword in SENSITIVE_KEYWORDS)


def sanitize_config(obj: Any) -> Any:
    if isinstance(obj, dict):
        sanitized: dict[str, Any] = {}
        for key, value in obj.items():
            if is_sensitive_key(str(key)):
                sanitized[key] = "[REDACTED]"
            else:
                sanitized[key] = sanitize_config(value)
        return sanitized

    if isinstance(obj, list):
        return [sanitize_config(item) for item in obj]

    return obj
