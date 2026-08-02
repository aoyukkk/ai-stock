from __future__ import annotations

from dataclasses import dataclass, field, replace


ADMISSION_V3_VERSION = "admission_v3_shadow_1"
ADMISSION_STATES = ("PASS_CORE", "PASS_EXPLORATORY", "REVIEW", "REJECT")

HARD_SAFETY_GATES = (
    "FUTURE_DATA",
    "SUSPENDED",
    "ST",
    "UNTRADEABLE",
    "BLACK_SWAN",
)


@dataclass(frozen=True)
class AdmissionV3Input:
    quant_score: float
    entry_timing_score: float
    sector_strength: float
    momentum_score: float
    strategy_probability: dict[str, float]
    strategy_status: str
    market_emotion_state: str = "UNKNOWN"
    risk_flags: tuple[str, ...] = ()
    future_data_detected: bool = False
    suspended: bool = False
    is_st: bool = False
    tradable: bool = True
    black_swan: bool = False
    industry_concentration: float = 0.0
    maximum_industry_concentration: float = 0.30
    maximum_correlation: float | None = None
    capacity_ratio: float = 1.0
    disabled_soft_gates: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class AdmissionV3Decision:
    admission_state: str
    hard_gate_results: dict[str, bool]
    risk_penalties: dict[str, dict[str, float]]
    opportunity_components: dict[str, float]
    portfolio_adjustments: dict[str, dict[str, float]]
    base_score: float
    opportunity_score: float
    final_score: float
    position_multiplier: float
    selected_reason: str | None
    rejected_reasons: list[str]
    largest_gate: str | None
    counterfactuals: dict[str, dict[str, float | str]]
    version: str = ADMISSION_V3_VERSION


@dataclass(frozen=True)
class AdmissionV3ShadowEV:
    expected_value_score: float
    risk_adjusted_opportunity_score: float


class AdmissionV3Engine:
    """Four-layer Admission V3 policy that is deliberately shadow-only."""

    def decide(self, value: AdmissionV3Input, *, include_counterfactuals: bool = True) -> AdmissionV3Decision:
        hard_results = {
            "FUTURE_DATA": not value.future_data_detected,
            "SUSPENDED": not value.suspended,
            "ST": not value.is_st,
            "UNTRADEABLE": value.tradable,
            "BLACK_SWAN": not value.black_swan,
        }
        failed_hard = [name for name, passed in hard_results.items() if not passed]
        if failed_hard:
            return AdmissionV3Decision(
                admission_state="REJECT",
                hard_gate_results=hard_results,
                risk_penalties={},
                opportunity_components=self._opportunity_components(value),
                portfolio_adjustments={},
                base_score=_bounded(value.quant_score),
                opportunity_score=self._opportunity_score(value),
                final_score=0.0,
                position_multiplier=0.0,
                selected_reason=None,
                rejected_reasons=failed_hard,
                largest_gate=failed_hard[0],
                counterfactuals={},
            )

        risk_penalties = self._risk_penalties(value)
        portfolio_adjustments = self._portfolio_adjustments(value)
        opportunity = self._opportunity_score(value)
        base_score = _bounded(value.quant_score) * 0.45 + opportunity * 0.55
        score_penalty = sum(item["score_penalty"] for item in risk_penalties.values())
        score_penalty += sum(item["score_penalty"] for item in portfolio_adjustments.values())
        final_score = max(0.0, min(100.0, base_score - score_penalty))
        position_multiplier = 1.0
        for item in (*risk_penalties.values(), *portfolio_adjustments.values()):
            position_multiplier *= item["position_multiplier"]
        position_multiplier = max(0.0, min(1.0, position_multiplier))
        state = self._state(value, base_score, final_score, bool(portfolio_adjustments))
        reasons = [] if state != "REJECT" else ["LOW_OPPORTUNITY_SCORE"]
        selected_reason = None if state == "REJECT" else self._selected_reason(value, state)
        all_effects = {**risk_penalties, **portfolio_adjustments}
        largest_gate = max(all_effects, key=lambda key: all_effects[key]["score_penalty"], default=None)
        counterfactuals = self._counterfactuals(value, all_effects) if include_counterfactuals else {}
        return AdmissionV3Decision(
            admission_state=state,
            hard_gate_results=hard_results,
            risk_penalties=risk_penalties,
            opportunity_components=self._opportunity_components(value),
            portfolio_adjustments=portfolio_adjustments,
            base_score=round(base_score, 6),
            opportunity_score=round(opportunity, 6),
            final_score=round(final_score, 6),
            position_multiplier=round(position_multiplier, 6),
            selected_reason=selected_reason,
            rejected_reasons=reasons,
            largest_gate=largest_gate,
            counterfactuals=counterfactuals,
        )

    @staticmethod
    def _opportunity_components(value: AdmissionV3Input) -> dict[str, float]:
        known = [
            probability
            for strategy, probability in value.strategy_probability.items()
            if strategy not in {"OTHER", "OPEN_SET"}
        ]
        strategy_probability = max(known, default=0.0) * 100.0
        return {
            "strategy_probability": round(_bounded(strategy_probability), 6),
            "entry_timing": round(_bounded(value.entry_timing_score), 6),
            "sector_strength": round(_bounded(value.sector_strength), 6),
            "momentum": round(_bounded(value.momentum_score), 6),
        }

    def _opportunity_score(self, value: AdmissionV3Input) -> float:
        components = self._opportunity_components(value)
        return (
            components["strategy_probability"] * 0.35
            + components["entry_timing"] * 0.25
            + components["sector_strength"] * 0.20
            + components["momentum"] * 0.20
        )

    @staticmethod
    def _risk_penalties(value: AdmissionV3Input) -> dict[str, dict[str, float]]:
        penalties: dict[str, dict[str, float]] = {}
        disabled = value.disabled_soft_gates
        if value.market_emotion_state == "RED" and "MARKET_RED" not in disabled:
            penalties["MARKET_RED"] = {"score_penalty": 10.0, "position_multiplier": 0.50}
        if set(value.risk_flags) & {"HIGH_POSITION_RISK", "HIGH_CHASE_RISK", "MOMENTUM_EXHAUSTION"} and "HIGH_POSITION_RISK" not in disabled:
            penalties["HIGH_POSITION_RISK"] = {"score_penalty": 8.0, "position_multiplier": 0.65}
        if set(value.risk_flags) & {"LIQUIDITY_RISK", "LOW_LIQUIDITY"} and "LIQUIDITY_RISK" not in disabled:
            penalties["LIQUIDITY_RISK"] = {"score_penalty": 6.0, "position_multiplier": 0.75}
        return penalties

    @staticmethod
    def _portfolio_adjustments(value: AdmissionV3Input) -> dict[str, dict[str, float]]:
        adjustments: dict[str, dict[str, float]] = {}
        disabled = value.disabled_soft_gates
        if value.industry_concentration > value.maximum_industry_concentration and "INDUSTRY_CONCENTRATION" not in disabled:
            adjustments["INDUSTRY_CONCENTRATION"] = {"score_penalty": 8.0, "position_multiplier": 0.70}
        if value.maximum_correlation is not None and value.maximum_correlation > 0.80 and "CORRELATION" not in disabled:
            adjustments["CORRELATION"] = {"score_penalty": 6.0, "position_multiplier": 0.75}
        if value.capacity_ratio < 1.0 and "CAPACITY" not in disabled:
            ratio = max(0.0, value.capacity_ratio)
            adjustments["CAPACITY"] = {"score_penalty": round((1.0 - ratio) * 10.0, 6), "position_multiplier": ratio}
        return adjustments

    @staticmethod
    def _state(value: AdmissionV3Input, base_score: float, final_score: float, portfolio_review: bool) -> str:
        if final_score >= 75 and not portfolio_review and value.strategy_status != "OPEN_SET":
            return "PASS_CORE"
        if final_score >= 65 and value.strategy_status != "OPEN_SET":
            return "PASS_EXPLORATORY"
        # OPEN_SET and soft penalties can only route to REVIEW, never directly block.
        if value.strategy_status == "OPEN_SET" or base_score >= 40:
            return "REVIEW"
        return "REJECT"

    @staticmethod
    def _selected_reason(value: AdmissionV3Input, state: str) -> str:
        probabilities = {
            key: score
            for key, score in value.strategy_probability.items()
            if key not in {"OTHER", "OPEN_SET"}
        }
        strategy = max(probabilities, key=probabilities.get, default="OPEN_SET")
        if value.strategy_status == "OPEN_SET":
            return "OPEN_SET retained for review; classification uncertainty is not a hard block."
        return f"{state}: strongest strategy probability is {strategy}."

    def _counterfactuals(
        self,
        value: AdmissionV3Input,
        effects: dict[str, dict[str, float]],
    ) -> dict[str, dict[str, float | str]]:
        output: dict[str, dict[str, float | str]] = {}
        for gate_name in effects:
            counterfactual = self.decide(
                replace(value, disabled_soft_gates=value.disabled_soft_gates | {gate_name}),
                include_counterfactuals=False,
            )
            output[gate_name] = {
                "admission_state": counterfactual.admission_state,
                "final_score": counterfactual.final_score,
                "score_delta": round(counterfactual.final_score - self.decide(value, include_counterfactuals=False).final_score, 6),
                "position_multiplier": counterfactual.position_multiplier,
            }
        return output


def _bounded(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def calculate_shadow_ev(value: AdmissionV3Input, decision: AdmissionV3Decision) -> AdmissionV3ShadowEV:
    """Compute additional Shadow diagnostics without changing V3 status or score."""

    open_set_probability = float(
        value.strategy_probability.get("OPEN_SET", value.strategy_probability.get("OTHER", 0.0))
    )
    expected_value = decision.opportunity_score * max(0.0, 1.0 - open_set_probability)
    score_penalty = sum(float(item["score_penalty"]) for item in decision.risk_penalties.values())
    score_penalty += sum(float(item["score_penalty"]) for item in decision.portfolio_adjustments.values())
    risk_adjusted = max(0.0, expected_value - score_penalty) * decision.position_multiplier
    return AdmissionV3ShadowEV(
        expected_value_score=round(_bounded(expected_value), 6),
        risk_adjusted_opportunity_score=round(_bounded(risk_adjusted), 6),
    )
