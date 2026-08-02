from __future__ import annotations

from typing import Any


EXECUTION_CONTRACT = {
    "entry_basis": "T_PLUS_1_OPEN_PLUS_CONFIGURED_SLIPPAGE",
    "horizons": ["D1", "D3", "D5", "D10"],
    "price_adjustment": "SAME_ADJ_FACTOR_BASIS",
    "tradability": "SAME_POINT_IN_TIME_TRADABILITY_GATE",
}


def build_forward_ab_manifest(v2_codes: list[str], v3_codes: list[str]) -> dict[str, Any]:
    return {
        "group_a": {"name": "V2_SECOND_STAGE", "stock_codes": v2_codes},
        "group_b": {"name": "V3_EVENT_OVERLAY_SECOND_STAGE", "stock_codes": v3_codes},
        "execution_contract": EXECUTION_CONTRACT,
        "connected_services": [
            "ranking_evaluation",
            "selection_performance",
            "forward_outcome",
        ],
        "automatic_weight_update": False,
        "automatic_promotion": False,
    }


def promote_demote_counterfactual(v2_codes: list[str], v3_codes: list[str]) -> dict[str, Any]:
    v2 = set(v2_codes)
    v3 = set(v3_codes)
    return {
        "promoted": sorted(v3 - v2),
        "demoted": sorted(v2 - v3),
        "kept": sorted(v2 & v3),
    }


def apply_shadow_deployment_limits(
    rows: list[dict[str, Any]],
    *,
    market_regime: str,
    max_active: int = 2,
) -> dict[str, list[dict[str, Any]]]:
    """Preserve V2-style regime and theme concentration limits.

    This remains advisory-only and never creates an order.
    """
    regime = str(market_regime or "RISK_OFF").upper()
    cap = 0 if regime == "BLOCKED" else min(max_active, 2 if regime == "RISK_OFF" else max_active)
    active: list[dict[str, Any]] = []
    watch: list[dict[str, Any]] = []
    themes: set[str] = set()
    for row in rows:
        risk = str(row.get("risk_action") or "WATCH_ONLY")
        theme = str(row.get("binding_cluster") or "UNKNOWN")
        eligible = risk in {"ALLOW", "PROMOTE", "KEEP"} and len(active) < cap and (regime != "RISK_OFF" or theme not in themes)
        if eligible:
            active.append({**row, "deployment_status": "ACTIVE_SHADOW"})
            themes.add(theme)
        else:
            watch.append({**row, "deployment_status": "WATCH_ONLY"})
    return {"active_shadow": active, "watch_pool": watch}
