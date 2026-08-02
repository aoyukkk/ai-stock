from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from statistics import fmean
from typing import Any

from sqlalchemy import select

from database.models.decision_explainability import AdmissionV3Result, StrategyTimingContract
from quant.explainability.factor_performance import RealizedReturn, RealizedReturnRepository, _latest_shadow_runs
from quant.explainability.timing_contract import compute_forward_return, validate_timing_contract


GATE_VALUE_VERSION = "gate_evaluation_service_v1_shadow"
GATE_NAMES = (
    "High Position Risk",
    "Market Emotion Gate",
    "Liquidity Gate",
    "Strategy Gate",
    "Data Gate",
)
STATE_ORDER = {"REJECT": 0, "REVIEW": 1, "PASS_EXPLORATORY": 2, "PASS_CORE": 3}


@dataclass(frozen=True)
class GateValueObservation:
    gate_name: str
    triggered: bool
    blocked: bool
    outcome: RealizedReturn | None


@dataclass(frozen=True)
class GateValueResult:
    gate_name: str
    trigger_count: int
    blocked_count: int
    future_return: float | None
    avoided_loss: float
    missed_gain: float
    net_gate_value: float
    false_positive_rate: float | None
    version: str = GATE_VALUE_VERSION


class GateEvaluationCalculator:
    def aggregate(self, observations: list[GateValueObservation]) -> list[GateValueResult]:
        output: list[GateValueResult] = []
        for gate_name in GATE_NAMES:
            triggered = [row for row in observations if row.gate_name == gate_name and row.triggered]
            blocked = [row for row in triggered if row.blocked]
            trigger_returns = [
                float(row.outcome.terminal_return)
                for row in triggered
                if row.outcome is not None and row.outcome.terminal_return is not None
            ]
            blocked_returns = [
                float(row.outcome.terminal_return)
                for row in blocked
                if row.outcome is not None and row.outcome.terminal_return is not None
            ]
            avoided_loss = sum(max(-value, 0.0) for value in blocked_returns)
            missed_gain = sum(max(value, 0.0) for value in blocked_returns)
            output.append(GateValueResult(
                gate_name=gate_name,
                trigger_count=len(triggered),
                blocked_count=len(blocked),
                future_return=round(fmean(trigger_returns), 6) if trigger_returns else None,
                avoided_loss=round(avoided_loss, 6),
                missed_gain=round(missed_gain, 6),
                net_gate_value=round(avoided_loss - missed_gain, 6),
                false_positive_rate=round(sum(value > 0 for value in blocked_returns) / len(blocked_returns), 6)
                if blocked_returns else None,
            ))
        return output


class GateEvaluationService:
    def __init__(self, session) -> None:
        self.session = session
        self.returns = RealizedReturnRepository(session)
        self.calculator = GateEvaluationCalculator()

    def evaluate(self, period_start: date, period_end: date) -> list[dict[str, Any]]:
        if period_start > period_end:
            raise ValueError("INVALID_GATE_EVALUATION_PERIOD")
        runs = _latest_shadow_runs(self.session, period_start, period_end)
        if not runs:
            return [_payload(row) for row in self.calculator.aggregate([])]
        results = list(self.session.scalars(
            select(AdmissionV3Result)
            .where(AdmissionV3Result.run_id.in_([row.run_id for row in runs]))
            .order_by(AdmissionV3Result.trade_date, AdmissionV3Result.stock_code)
        ))
        contracts = {
            row.id: row
            for row in self.session.scalars(
                select(StrategyTimingContract).where(
                    StrategyTimingContract.id.in_({item.timing_contract_id for item in results})
                )
            )
        }
        outcomes = self.returns.load(period_start, period_end)
        observations: list[GateValueObservation] = []
        for row in results:
            contract = contracts.get(row.timing_contract_id)
            if contract is None:
                raise ValueError("INVALID_TIMING_CONTRACT")
            validate_timing_contract(contract)
            outcome = outcomes.get((row.trade_date, row.stock_code))
            if outcome is not None:
                outcome = compute_forward_return(contract, lambda value=outcome: value)
            observations.extend(self._observations(row, outcome))
        ranked = self.calculator.aggregate(observations)
        ranked.sort(key=lambda row: (-row.net_gate_value, row.gate_name))
        return [_payload(row) for row in ranked]

    @staticmethod
    def _observations(row: AdmissionV3Result, outcome: RealizedReturn | None) -> list[GateValueObservation]:
        risk = row.risk_penalties or {}
        portfolio = row.portfolio_adjustments or {}
        counterfactuals = row.counterfactuals or {}
        hard = row.hard_gate_results or {}
        definitions = {
            "High Position Risk": (["HIGH_POSITION_RISK"], "HIGH_POSITION_RISK" in risk),
            "Market Emotion Gate": (["MARKET_RED"], "MARKET_RED" in risk),
            "Liquidity Gate": (
                ["LIQUIDITY_RISK", "CAPACITY"],
                "LIQUIDITY_RISK" in risk or "CAPACITY" in portfolio,
            ),
            "Strategy Gate": ([], row.strategy_status == "OPEN_SET"),
            "Data Gate": ([], any(not passed for passed in hard.values())),
        }
        output: list[GateValueObservation] = []
        for gate_name, (keys, triggered) in definitions.items():
            if gate_name == "Data Gate":
                blocked = triggered and row.admission_state == "REJECT"
            elif gate_name == "Strategy Gate":
                blocked = triggered and row.admission_state == "REJECT"
            else:
                blocked = any(_counterfactual_improves(row.admission_state, counterfactuals.get(key)) for key in keys)
            output.append(GateValueObservation(gate_name, triggered, blocked, outcome))
        return output


def _counterfactual_improves(current: str, value: dict[str, Any] | None) -> bool:
    if not value:
        return False
    return STATE_ORDER.get(str(value.get("admission_state")), -1) > STATE_ORDER.get(current, -1)


def _payload(row: GateValueResult) -> dict[str, Any]:
    return {
        "gate_name": row.gate_name,
        "trigger_count": row.trigger_count,
        "blocked_count": row.blocked_count,
        "future_return": row.future_return,
        "avoided_loss": row.avoided_loss,
        "missed_gain": row.missed_gain,
        "net_gate_value": row.net_gate_value,
        "false_positive_rate": row.false_positive_rate,
        "version": row.version,
    }
