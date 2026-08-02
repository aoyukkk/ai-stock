from __future__ import annotations

from typing import Any, Mapping

from temporal.freshness import canonical_hash


REGIME_DEPLOYMENT_CONTRACT_VERSION = "market-regime-deployment-v1"


def resolve_deployment_regime(
    market_regime: Mapping[str, Any] | None,
    *,
    requested_deployment_regime: str | None = None,
) -> dict[str, Any]:
    calculated = str((market_regime or {}).get("regime") or "").upper()
    fallback_used = not calculated
    deployment = str(requested_deployment_regime or calculated or "RISK_OFF").upper()
    if calculated and deployment != calculated:
        raise ValueError("MARKET_REGIME_DEPLOYMENT_MISMATCH")
    return {
        "calculated_regime": calculated or None,
        "deployment_regime": deployment,
        "fallback_used": fallback_used,
        "input_hash": canonical_hash(market_regime or {"status": "MISSING"}),
        "contract_version": REGIME_DEPLOYMENT_CONTRACT_VERSION,
    }
