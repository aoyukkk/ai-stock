from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import floor
from statistics import fmean
from typing import Any, Iterable, Mapping


class HeldAction(StrEnum):
    CONTINUE_HOLD = "CONTINUE_HOLD"
    HOLD_WITH_TIGHT_STOP = "HOLD_WITH_TIGHT_STOP"
    REDUCE_POSITION = "REDUCE_POSITION"
    EXIT_NEXT_SESSION = "EXIT_NEXT_SESSION"
    EXIT_WHEN_TRADABLE = "EXIT_WHEN_TRADABLE"
    T_PLUS_ONE_LOCKED_EXIT_PLAN = "T_PLUS_ONE_LOCKED_EXIT_PLAN"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    DATA_INSUFFICIENT = "DATA_INSUFFICIENT"


class CandidateAction(StrEnum):
    PREPARE_ENTRY = "PREPARE_ENTRY"
    KEEP_WATCH = "KEEP_WATCH"
    DO_NOT_CHASE = "DO_NOT_CHASE"
    REMOVE_FROM_POOL = "REMOVE_FROM_POOL"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    DATA_INSUFFICIENT = "DATA_INSUFFICIENT"


HELD_ACTION_ORDER = {
    HeldAction.CONTINUE_HOLD: 0,
    HeldAction.HOLD_WITH_TIGHT_STOP: 1,
    HeldAction.REDUCE_POSITION: 2,
    HeldAction.EXIT_NEXT_SESSION: 3,
    HeldAction.T_PLUS_ONE_LOCKED_EXIT_PLAN: 3,
    HeldAction.EXIT_WHEN_TRADABLE: 4,
    HeldAction.MANUAL_REVIEW: 5,
    HeldAction.DATA_INSUFFICIENT: 5,
}


@dataclass(frozen=True)
class ActionHealthInput:
    base_quant_score: float | None = None
    flash_score: float | None = None
    pro_score: float | None = None
    risk_health_score: float | None = None
    closing_structure_score: float | None = None
    relative_strength_score: float | None = None
    regime_fit_score: float | None = None
    position_state_score: float | None = None


@dataclass(frozen=True)
class PositionFacts:
    held: bool
    quantity: int | None = None
    available_quantity: int | None = None
    target_day_sellable_quantity: int | None = None
    bought_today_quantity: int = 0
    suspended: bool = False
    limit_down_locked: bool = False
    position_data_complete: bool = True


@dataclass(frozen=True)
class HardGateInput:
    material_conflict: bool = False
    critical_data_missing: bool = False
    hard_risk: bool = False
    stop_breached: bool = False
    exit_condition: bool = False


@dataclass(frozen=True)
class ActionDecision:
    action_health_score: float | None
    action: str
    hard_gate_status: str
    requires_manual_review: bool
    suggested_reduce_percent: float | None
    suggested_reduce_quantity: int | None
    reasons: list[str]
    risks: list[str]


class PostCloseActionHealthEngine:
    COMPONENT_WEIGHTS = {
        "selection_quality": 0.30,
        "risk_health": 0.20,
        "closing_structure": 0.15,
        "relative_strength": 0.15,
        "market_regime_fit": 0.10,
        "position_state": 0.10,
    }

    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config = dict(config)
        self.thresholds = self.config.get("thresholds", {})
        self.candidate_thresholds = self.config.get("candidate_thresholds", {})
        self.reduce = self.config.get("reduce", {})

    def health_score(self, value: ActionHealthInput) -> float | None:
        selection = _weighted_available(
            ((value.base_quant_score, 0.50), (value.flash_score, 0.25), (value.pro_score, 0.25))
        )
        components = {
            "selection_quality": selection,
            "risk_health": value.risk_health_score,
            "closing_structure": value.closing_structure_score,
            "relative_strength": value.relative_strength_score,
            "market_regime_fit": value.regime_fit_score,
            "position_state": value.position_state_score,
        }
        available = [(score, self.COMPONENT_WEIGHTS[name]) for name, score in components.items() if score is not None]
        return round(_weighted_available(available), 6) if available else None

    def decide(self, value: ActionHealthInput, position: PositionFacts, hard_gate: HardGateInput) -> ActionDecision:
        health = self.health_score(value)
        gate = self._hard_gate(position, hard_gate)
        if gate:
            action, status, reason = gate
            return ActionDecision(health, action, status, action in {HeldAction.MANUAL_REVIEW, CandidateAction.MANUAL_REVIEW}, None, None, [reason], [reason])
        if health is None:
            action = HeldAction.DATA_INSUFFICIENT if position.held else CandidateAction.DATA_INSUFFICIENT
            return ActionDecision(None, action, "PASS", False, None, None, ["ACTION_INPUT_INSUFFICIENT"], ["DATA_INSUFFICIENT"])
        if position.held:
            return self._held(health, position)
        return self._candidate(health, value)

    def _hard_gate(self, position: PositionFacts, value: HardGateInput) -> tuple[str, str, str] | None:
        if value.material_conflict or value.critical_data_missing or not position.position_data_complete:
            action = HeldAction.MANUAL_REVIEW if position.held else CandidateAction.MANUAL_REVIEW
            return action, "MANUAL_REVIEW_REQUIRED", "CRITICAL_DATA_CONFLICT_OR_MISSING"
        if not position.held:
            return None
        exit_required = value.hard_risk or value.stop_breached or value.exit_condition
        if not exit_required:
            return None
        if position.suspended or position.limit_down_locked:
            return HeldAction.EXIT_WHEN_TRADABLE, "EXIT_BLOCKED", "EXIT_CONDITION_BUT_NOT_TRADABLE"
        if position.bought_today_quantity > 0 and (position.available_quantity or 0) <= 0:
            return HeldAction.T_PLUS_ONE_LOCKED_EXIT_PLAN, "T_PLUS_ONE_LOCKED_TODAY", "EXIT_CONDITION_T_PLUS_ONE_LOCKED"
        return HeldAction.EXIT_NEXT_SESSION, "HARD_EXIT", "HARD_EXIT_CONDITION"

    def _held(self, health: float, position: PositionFacts) -> ActionDecision:
        if health >= float(self.thresholds.get("continue_hold", 75)):
            return ActionDecision(health, HeldAction.CONTINUE_HOLD, "PASS", False, None, None, ["ACTION_HEALTH_STRONG"], [])
        if health >= float(self.thresholds.get("hold_with_tight_stop", 60)):
            return ActionDecision(health, HeldAction.HOLD_WITH_TIGHT_STOP, "PASS", False, None, None, ["ACTION_HEALTH_MODERATE"], ["TIGHTEN_STOP"])
        severity = "mild" if health >= float(self.thresholds.get("reduce_position", 45)) else "severe"
        percent = float(self.reduce.get(f"{severity}_percent", 0.25 if severity == "mild" else 0.75))
        quantity = self.reduce_quantity(position.target_day_sellable_quantity or 0, percent)
        action = HeldAction.REDUCE_POSITION if severity == "mild" else HeldAction.EXIT_NEXT_SESSION
        return ActionDecision(health, action, "PASS", False, percent, quantity, ["ACTION_HEALTH_WEAK"], ["POSITION_REDUCTION_OR_EXIT"])

    def _candidate(self, health: float, value: ActionHealthInput) -> ActionDecision:
        if health >= float(self.candidate_thresholds.get("prepare_entry", 70)):
            action = CandidateAction.DO_NOT_CHASE if (value.closing_structure_score or 50) < 40 else CandidateAction.PREPARE_ENTRY
        elif health >= float(self.candidate_thresholds.get("keep_watch", 50)):
            action = CandidateAction.KEEP_WATCH
        elif health < float(self.candidate_thresholds.get("remove_below", 40)):
            action = CandidateAction.REMOVE_FROM_POOL
        else:
            action = CandidateAction.DO_NOT_CHASE
        return ActionDecision(health, action, "PASS", False, None, None, ["CANDIDATE_RULE_SCORE"], [])

    def reduce_quantity(self, sellable_quantity: int, percent: float) -> int:
        lot = int(self.reduce.get("lot_size", 100))
        raw = max(0, floor(max(0, sellable_quantity) * max(0.0, min(1.0, percent))))
        return min(max(0, sellable_quantity), raw // lot * lot)


def enforce_conservative_pro_review(baseline_action: str, reviewed_action: str, *, held: bool) -> str:
    if not held:
        allowed = {item.value for item in CandidateAction}
        return reviewed_action if reviewed_action in allowed else CandidateAction.MANUAL_REVIEW
    try:
        baseline = HeldAction(baseline_action)
        reviewed = HeldAction(reviewed_action)
    except ValueError:
        return HeldAction.MANUAL_REVIEW
    return reviewed if HELD_ACTION_ORDER[reviewed] >= HELD_ACTION_ORDER[baseline] else baseline


def target_day_sellable(quantity: int, available_quantity: int, bought_today_quantity: int, *, target_is_next_session: bool, suspended: bool = False) -> int:
    if suspended:
        return 0
    current = max(0, min(quantity, available_quantity))
    return max(0, quantity) if target_is_next_session else current


def _weighted_available(values: Iterable[tuple[float | None, float]]) -> float | None:
    available = [(max(0.0, min(100.0, float(value))), float(weight)) for value, weight in values if value is not None]
    total_weight = sum(weight for _, weight in available)
    if not available or total_weight <= 0:
        return None
    return sum(value * weight for value, weight in available) / total_weight
