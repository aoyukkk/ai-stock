from __future__ import annotations

from dataclasses import dataclass
from statistics import fmean

from quant.explainability.timing_contract import compute_forward_return


GATE_EVALUATION_VERSION = "gate_evaluation_v3_shadow_1"


@dataclass(frozen=True)
class GateObservation:
    timing_contract: object
    blocked: bool
    future_return_calculator: object


@dataclass(frozen=True)
class GateEvaluationResult:
    gate_name: str
    blocked_count: int
    evaluated_count: int
    future_return: float | None
    avoided_loss: float
    missed_gain: float
    net_gate_value: float
    version: str = GATE_EVALUATION_VERSION


class GateCounterfactualEvaluator:
    def evaluate(self, gate_name: str, observations: list[GateObservation]) -> GateEvaluationResult:
        blocked_returns: list[float] = []
        for observation in observations:
            if not observation.blocked:
                continue
            value = float(compute_forward_return(observation.timing_contract, observation.future_return_calculator))
            blocked_returns.append(value)
        avoided_loss = sum(max(-value, 0.0) for value in blocked_returns)
        missed_gain = sum(max(value, 0.0) for value in blocked_returns)
        return GateEvaluationResult(
            gate_name=gate_name,
            blocked_count=len(blocked_returns),
            evaluated_count=len(observations),
            future_return=round(fmean(blocked_returns), 6) if blocked_returns else None,
            avoided_loss=round(avoided_loss, 6),
            missed_gain=round(missed_gain, 6),
            net_gate_value=round(avoided_loss - missed_gain, 6),
        )
