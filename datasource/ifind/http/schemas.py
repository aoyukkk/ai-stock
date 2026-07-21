from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class IFindHttpAuthResult:
    status: str
    source: str
    expires_at: datetime | None
    latency_ms: int | None
    error_category: str | None
    response_schema_hash: str | None


@dataclass(frozen=True)
class IFindHttpResponse:
    endpoint: str
    status_code: int
    latency_ms: int
    provider_code: object
    provider_message: str
    schema_hash: str
    payload: dict[str, Any]
