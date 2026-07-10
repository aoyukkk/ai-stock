from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

from quant.factors import RiskFactorCalculator
from quant.price_limit import LIMIT_STATUS_SCORES
from tests.test_quant_factors import build_input


RISK_CONFIG = {
    "price_limit": {"enabled": True},
    "internal_weights": {
        "volatility": 0.30,
        "drawdown": 0.25,
        "liquidity": 0.15,
        "financial": 0.15,
        "price_limit": 0.15,
    },
}


def test_disabled_price_limit_mode_reproduces_legacy_risk_score() -> None:
    data = build_input()
    baseline, _ = RiskFactorCalculator().calculate(data)
    disabled, _ = RiskFactorCalculator({"price_limit": {"enabled": False}}).calculate(data)

    assert disabled == baseline


def test_price_limit_risk_is_not_a_positive_momentum_reward() -> None:
    data = build_input()

    def score(status: str) -> Decimal:
        candidate = deepcopy(data)
        candidate.price_limit_risk = {
            "limit_status": status,
            "price_limit_risk_score": LIMIT_STATUS_SCORES[status],
        }
        return RiskFactorCalculator(RISK_CONFIG).calculate(candidate)[0]

    assert score("NORMAL") > score("AT_LIMIT_UP") > score("CONSECUTIVE_LIMIT_UP")
    assert score("CONSECUTIVE_LIMIT_UP") > score("AT_LIMIT_DOWN") > score("CONSECUTIVE_LIMIT_DOWN")
