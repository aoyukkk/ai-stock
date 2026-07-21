from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class IFindIntegrationMode(StrEnum):
    DISABLED = "DISABLED"
    SHADOW = "SHADOW"
    ACTIVE_SELECTED_POOL = "ACTIVE_SELECTED_POOL"
    ACTIVE_INDEX_ONLY = "ACTIVE_INDEX_ONLY"


class IFindPersistencePurpose(StrEnum):
    SHADOW_VALIDATION = "SHADOW_VALIDATION"
    REALTIME_MONITOR = "REALTIME_MONITOR"
    MARKET_REVIEW_INDEX = "MARKET_REVIEW_INDEX"
    PRODUCTION_INPUT = "PRODUCTION_INPUT"


class IFindCapabilityStatus(StrEnum):
    VERIFIED = "VERIFIED"
    NOT_VERIFIED = "NOT_VERIFIED"
    CAPABILITY_NOT_VERIFIED = "CAPABILITY_NOT_VERIFIED"


@dataclass(frozen=True)
class ProviderRegistration:
    provider_name: str
    transport: str
    capability: str
    enabled: bool = False
    integration_mode: IFindIntegrationMode = IFindIntegrationMode.SHADOW
    verified_at: str | None = "2026-07-14"
    capability_status: IFindCapabilityStatus = IFindCapabilityStatus.VERIFIED
    observed_session_status: str = "CLOSED_SESSION_FINAL"
    fallback_provider: str = "tushare"
    schema_version: str = "ifind-p0-v1"


class IFindProviderRegistry:
    """Static metadata registry; it never routes official business results."""

    def __init__(self, *, enabled: bool = False, mode: IFindIntegrationMode = IFindIntegrationMode.SHADOW) -> None:
        self._entries = {
            key: ProviderRegistration(
                provider_name=key,
                transport="HTTP",
                capability=capability,
                enabled=enabled,
                integration_mode=mode,
            )
            for key, capability in (
                ("ifind_http_index", "index_daily,index_realtime"),
                ("ifind_http_realtime", "stock_realtime"),
                ("ifind_http_minute", "minute_bars"),
            )
        }

    def get(self, name: str) -> ProviderRegistration:
        return self._entries[name]

    def entries(self) -> list[ProviderRegistration]:
        return list(self._entries.values())

    def as_dict(self) -> list[dict[str, Any]]:
        return [{"provider_name": item.provider_name, "transport": item.transport, "capability": item.capability,
                 "enabled": item.enabled, "integration_mode": item.integration_mode.value,
                 "verified_at": item.verified_at, "capability_status": item.capability_status.value,
                 "observed_session_status": item.observed_session_status, "fallback_provider": item.fallback_provider,
                 "schema_version": item.schema_version} for item in self.entries()]


def assert_persistence_purpose(purpose: IFindPersistencePurpose) -> None:
    if purpose == IFindPersistencePurpose.PRODUCTION_INPUT:
        raise RuntimeError("IFIND_PRODUCTION_INPUT_NOT_APPROVED")
