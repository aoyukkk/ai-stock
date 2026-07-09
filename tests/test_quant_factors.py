from decimal import Decimal

import pytest

from datasource.service import DataSourceService
from quant.config import QuantConfig, load_quant_config
from quant.exceptions import InvalidQuantConfigError
from quant.factors import (
    CapitalFactorCalculator,
    EmotionFactorCalculator,
    MomentumFactorCalculator,
    RiskFactorCalculator,
    TechnicalFactorCalculator,
)
from quant.ranking import QuantRankingEngine
from quant.schemas import QuantFactorInput


def build_input() -> QuantFactorInput:
    service = DataSourceService()
    stock = service.get_stock_universe()[0]
    quote = service.get_realtime_quotes([stock.stock_code])[0]
    market_provider = service.registry.get_default_market_provider()
    market_emotion = market_provider.get_market_emotion()
    return QuantFactorInput(
        stock_code=stock.stock_code,
        stock_name=stock.name,
        industry=stock.industry,
        realtime_quote=quote,
        kline_bars=market_provider.get_kline(stock.stock_code, market_emotion.trade_date.replace(day=1), market_emotion.trade_date),
        finance_snapshot=market_provider.get_finance(stock.stock_code),
        capital_flow=market_provider.get_capital_flow(stock.stock_code),
        market_emotion=market_emotion,
    )


def assert_score_range(score: Decimal) -> None:
    assert Decimal("0") <= score <= Decimal("100")


def test_factor_calculators_output_0_to_100_scores() -> None:
    data = build_input()
    calculators = [
        TechnicalFactorCalculator(),
        CapitalFactorCalculator(),
        EmotionFactorCalculator(),
        MomentumFactorCalculator(),
        RiskFactorCalculator(),
    ]

    for calculator in calculators:
        score, details = calculator.calculate(data)
        assert_score_range(score)
        assert details
        assert all(Decimal("0") <= detail.score <= Decimal("100") for detail in details)


def test_risk_health_score_higher_for_lower_risk() -> None:
    data = build_input()
    risk_score, _ = RiskFactorCalculator().calculate(data)

    assert_score_range(risk_score)
    assert risk_score > Decimal("0")


def test_weight_validation_ok_and_invalid() -> None:
    QuantRankingEngine(config=load_quant_config()).validate_weights()

    invalid_config = QuantConfig(
        raw={
            "quant_factor": {
                "factor_version": "invalid",
                "weights": {
                    "technical": 1,
                    "capital": 1,
                    "emotion": 1,
                    "momentum": 1,
                    "risk": 1,
                },
                "validation": {"weight_tolerance": 0.0001},
            }
        }
    )
    with pytest.raises(InvalidQuantConfigError, match="weights must sum"):
        QuantRankingEngine(config=invalid_config).validate_weights()
