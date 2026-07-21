from __future__ import annotations

from copy import deepcopy
from typing import Any

from backend.core.config import AppConfig, get_app_config


class EntryTimingV2ConfigService:
    """Resolve preregistered V2.1 config through the application config service."""

    def __init__(self, app_config: AppConfig | None = None) -> None:
        self.app_config = app_config or get_app_config()

    def get(self) -> dict[str, Any]:
        value = deepcopy(
            self.app_config.config_files.get("entry_timing_v2_1", {}).get("entry_timing_v2_1", {})
        )
        if not value:
            raise ValueError("ENTRY_TIMING_V2_CONFIG_MISSING")
        weights = value.get("weights") or {}
        ranking = value.get("admission_ranking_weights") or {}
        if abs(sum(float(item) for item in weights.values()) - 1.0) > 1e-9:
            raise ValueError("ENTRY_TIMING_V2_WEIGHT_SUM_INVALID")
        if abs(sum(float(item) for item in ranking.values()) - 1.0) > 1e-9:
            raise ValueError("ADMISSION_V2_WEIGHT_SUM_INVALID")
        safety = value.get("safety") or {}
        if any(bool(safety.get(key)) for key in (
            "actionable", "scheduler_enabled", "create_orders", "create_virtual_orders",
            "real_trading_enabled", "external_calls_enabled", "llm_calls_enabled",
            "parameter_search_enabled",
        )):
            raise ValueError("ENTRY_TIMING_V2_SAFETY_CONFIG_INVALID")
        return value
