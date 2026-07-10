from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from quant.config import QuantConfig, load_quant_config
from quant.exceptions import InvalidQuantConfigError
from quant.factors import (
    CapitalFactorCalculator,
    EmotionFactorCalculator,
    MomentumFactorCalculator,
    RiskFactorCalculator,
    TechnicalFactorCalculator,
)
from quant.normalizer import clamp_score
from quant.schemas import QuantFactorInput, QuantRankingResult, QuantScoreResult


class QuantRankingEngine:
    def __init__(self, config: QuantConfig | None = None) -> None:
        self.config = config or load_quant_config()
        self.technical_calculator = TechnicalFactorCalculator(
            self.config.raw.get("technical_factor", {})
        )
        self.capital_calculator = CapitalFactorCalculator()
        self.emotion_calculator = EmotionFactorCalculator()
        momentum_config = self.config.raw.get("momentum_factor", {})
        self.momentum_calculator = MomentumFactorCalculator(
            r5_weight=Decimal(str(momentum_config.get("r5_weight", "0.60"))),
            r20_weight=Decimal(str(momentum_config.get("r20_weight", "0.40"))),
        )
        self.risk_calculator = RiskFactorCalculator(
            self.config.raw.get("risk_factor", {})
        )

    def validate_weights(self) -> None:
        weights = self.config.weights
        total = sum(weights.values(), Decimal("0"))
        if abs(total - Decimal("1")) > self.config.weight_tolerance:
            raise InvalidQuantConfigError(
                f"Quant factor weights must sum to 1.0, got {total}."
            )

    def calculate_stock_score(self, input_data: QuantFactorInput) -> QuantScoreResult:
        self.validate_weights()
        technical_score, technical_details = self.technical_calculator.calculate(input_data)
        capital_score, capital_details = self.capital_calculator.calculate(input_data)
        emotion_score, emotion_details = self.emotion_calculator.calculate(input_data)
        momentum_score, momentum_details = self.momentum_calculator.calculate(input_data)
        risk_score, risk_details = self.risk_calculator.calculate(input_data)

        weights = self.config.weights
        total_score = clamp_score(
            technical_score * weights["technical"]
            + capital_score * weights["capital"]
            + emotion_score * weights["emotion"]
            + momentum_score * weights["momentum"]
            + risk_score * weights["risk"]
        )
        details = (
            technical_details
            + capital_details
            + emotion_details
            + momentum_details
            + risk_details
        )
        return QuantScoreResult(
            stock_code=input_data.stock_code,
            stock_name=input_data.stock_name,
            industry=input_data.industry,
            technical_score=technical_score,
            capital_score=capital_score,
            emotion_score=emotion_score,
            momentum_score=momentum_score,
            risk_score=risk_score,
            total_score=total_score,
            factor_details=details,
            reason=(
                "Quant score combines technical, capital, emotion, momentum, "
                "and risk-health factors without LLM calls."
            ),
            factor_version=self.config.factor_version,
        )

    def rank_stocks(
        self,
        inputs: list[QuantFactorInput],
        top_q: int,
    ) -> QuantRankingResult:
        results = [self.calculate_stock_score(item) for item in inputs]
        results.sort(key=lambda item: item.total_score, reverse=True)
        selected = results[: min(top_q, len(results))]
        for index, item in enumerate(selected, start=1):
            item.rank = index
        return QuantRankingResult(
            generated_at=datetime.now(timezone.utc),
            universe_size=len(inputs),
            requested_top_q=top_q,
            returned_count=len(selected),
            factor_version=self.config.factor_version,
            results=selected,
        )
