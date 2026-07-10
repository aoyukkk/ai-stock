from __future__ import annotations

import os
import re
from typing import Any


SENSITIVE_KEYWORDS = (
    "api_key",
    "secret",
    "password",
    "username",
    "credential",
    "authorization",
    "database_url",
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


_BEARER_PATTERN = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+\-/=]+")
_URL_CREDENTIAL_PATTERN = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)(?P<user>[^:/\s]+):(?P<password>[^@/\s]+)@", re.I)


def redact_sensitive_text(value: Any) -> str:
    """Redact known environment secrets and common credential forms from errors/logs."""
    text = str(value)
    text = _BEARER_PATTERN.sub("Bearer [REDACTED]", text)
    text = _URL_CREDENTIAL_PATTERN.sub(r"\g<scheme>\g<user>:[REDACTED]@", text)
    for key, secret in os.environ.items():
        if not secret or len(secret) < 6 or not is_sensitive_key(key):
            continue
        text = text.replace(secret, "[REDACTED]")
    return text
