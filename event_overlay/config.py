from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "config" / "event_overlay_v3.yaml"


def load_event_overlay_config(path: Path = DEFAULT_PATH) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    weights = payload.get("weights")
    scoring = payload.get("scoring") or {}
    if weights is not None:
        weight_values = [float(value) for value in weights.values()]
        if (
            not all(math.isfinite(value) for value in weight_values)
            or abs(sum(weight_values) - 1.0) > 1e-9
        ):
            raise ValueError("EVENT_OVERLAY_WEIGHT_SUM_INVALID")
    elif scoring.get("mode") != "CONFIDENCE_GATED_EVENT_DELTA_V3_1":
        raise ValueError("EVENT_OVERLAY_SCORING_POLICY_MISSING")
    if scoring:
        max_delta = float(scoring.get("max_event_delta") or 0)
        if not math.isfinite(max_delta) or not 0 < max_delta <= 10:
            raise ValueError("EVENT_OVERLAY_MAX_EVENT_DELTA_INVALID")
        penalties = [
            float(value)
            for value in (scoring.get("risk_penalties") or {}).values()
        ]
        if not all(math.isfinite(value) for value in penalties):
            raise ValueError("EVENT_OVERLAY_RISK_PENALTY_INVALID")
        required_actions = {"ALLOW", "PROMOTE", "KEEP", "DEMOTE", "WATCH_ONLY", "BLOCK"}
        if required_actions - set(scoring.get("risk_penalties") or {}):
            raise ValueError("EVENT_OVERLAY_RISK_PENALTY_MISSING")
    if payload["production_or_shadow"] != "SHADOW":
        raise ValueError("EVENT_OVERLAY_MUST_REMAIN_SHADOW")
    freshness = payload.get("freshness") or {}
    if freshness.get("unknown_publish_time_eligible") is True:
        raise ValueError("UNKNOWN_PUBLISH_TIME_MUST_NOT_SCORE")
    return payload
