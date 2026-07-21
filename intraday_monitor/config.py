from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import Any


DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "integration_mode": "SHADOW",
    "observation_only": True,
    "pool": {"default_max_stocks": 20, "hard_max_stocks": 50, "provider_architecture_max_stocks": 100},
    "refresh": {
        "critical_snapshot_seconds": 20, "high_snapshot_seconds": 30,
        "normal_snapshot_seconds": 60, "low_snapshot_seconds": 120,
        "index_snapshot_seconds": 30, "minute_refresh_seconds": 60,
        "minute_auto_stocks_limit": 5, "minute_on_trigger": True,
        "minute_on_detail_open": True,
    },
    "alerts": {
        "default_cooldown_seconds": 300, "critical_cooldown_seconds": 60,
        "require_consecutive_hits": 2, "consecutive_hit_interval_seconds": 10,
        "hysteresis_percent": 0.2, "max_same_rule_alerts_per_day": 5,
    },
    "midday_protection": {
        "stop_new_normal_requests_at": "11:29:30", "force_pause_at": "11:30:00",
        "midday_priority_start_at": "11:30:00", "midday_latest_start_at": "12:50:00",
        "afternoon_recheck_start_at": "13:00:30", "afternoon_monitor_resume_after_recheck": True,
    },
    "security": {"allow_order_creation": False, "allow_auto_action": False, "allow_llm_in_refresh_loop": False},
}


def load_monitor_config(app_config: Any | None = None) -> dict[str, Any]:
    raw = {}
    if app_config is not None:
        raw = app_config.config_files.get("intraday_monitor", {}).get("intraday_monitor", {})
    merged = _deep_merge(DEFAULTS, raw)
    validate_monitor_config(merged)
    return merged


def validate_monitor_config(config: dict[str, Any]) -> None:
    if config.get("integration_mode") != "SHADOW" or not config.get("observation_only", True):
        raise ValueError("INTRADAY_MONITOR_MUST_BE_SHADOW_OBSERVATION_ONLY")
    security = config.get("security", {})
    if any(security.get(key, False) for key in ("allow_order_creation", "allow_auto_action", "allow_llm_in_refresh_loop")):
        raise ValueError("INTRADAY_MONITOR_UNSAFE_SECURITY_CONFIG")
    pool = config.get("pool", {})
    if int(pool.get("default_max_stocks", 0)) > int(pool.get("hard_max_stocks", 0)):
        raise ValueError("INTRADAY_MONITOR_POOL_LIMIT_INVALID")
    for key in ("stop_new_normal_requests_at", "force_pause_at", "midday_priority_start_at", "midday_latest_start_at", "afternoon_recheck_start_at"):
        time.fromisoformat(str(config["midday_protection"][key]))


def _deep_merge(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    result = {key: (dict(value) if isinstance(value, dict) else value) for key, value in left.items()}
    for key, value in right.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
