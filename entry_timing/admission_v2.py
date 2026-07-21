from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AdmissionV2Decision:
    admission_status: str
    admission_ranking_score: float | None
    market_gate_status: str
    block_reasons: list[str]
    review_reasons: list[str]
    effective_pass_threshold: float | None
    effective_review_threshold: float
    requires_manual_review: bool


class StrategyAwareAdmissionEngine:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def decide(self, *, quant_score: float, risk_score: float | None, timing_score: float | None, strategy_id: str, strategy_fit: float, classification_status: str, market_gate_status: str, market_gate_reasons: list[str], threshold_increment: float, risk_flags: list[str], data_quality: float, data_conflicted: bool = False) -> AdmissionV2Decision:
        thresholds = self.config["thresholds"][strategy_id]
        pass_threshold = float(thresholds["pass"]) + threshold_increment if thresholds.get("pass") is not None else None
        review_threshold = float(thresholds["review"])
        blocks: list[str] = []
        reviews: list[str] = []
        if data_conflicted: blocks.append("DATA_CONFLICTED")
        if data_quality < float(self.config["minimum_data_quality"]): blocks.append("DATA_GATE_FAILED")
        if quant_score < float(self.config["minimum_quant_score"]): blocks.append("QUANT_GATE_FAILED")
        if risk_score is None or risk_score < float(self.config["minimum_risk_score"]): blocks.append("EXISTING_RISK_GATE_FAILED")
        hard_position = set(risk_flags) & {"HIGH_CHASE_RISK", "MOMENTUM_EXHAUSTION", "DISTRIBUTION_RISK", "LIMIT_RISK"}
        if hard_position: blocks.extend(sorted(hard_position))
        if classification_status == "DATA_INSUFFICIENT": blocks.append("STRATEGY_DATA_INSUFFICIENT")
        if market_gate_status == "BLOCK": blocks.extend(market_gate_reasons)
        if strategy_fit < float(self.config["minimum_strategy_fit_for_review"]): blocks.append("STRATEGY_FIT_BELOW_REVIEW")
        if timing_score is None or timing_score < review_threshold: blocks.append("ENTRY_TIMING_BELOW_STRATEGY_REVIEW")
        weights = self.config["admission_ranking_weights"]
        ranking = None if timing_score is None else (
            quant_score * float(weights["baseline_quant"])
            + timing_score * float(weights["entry_timing_v2"])
            + strategy_fit * float(weights["strategy_fit"])
        )
        if blocks:
            return AdmissionV2Decision("BLOCK", _round(ranking), market_gate_status, list(dict.fromkeys(blocks)), [], pass_threshold, review_threshold, True)
        can_pass = (
            strategy_id != "UNCLASSIFIED" and market_gate_status == "PASS"
            and pass_threshold is not None and timing_score >= pass_threshold
            and strategy_fit >= float(self.config["minimum_strategy_fit_for_pass"])
            and ranking is not None and ranking >= float(self.config["minimum_admission_ranking_for_pass"])
        )
        if can_pass:
            return AdmissionV2Decision("PASS", _round(ranking), market_gate_status, [], [], pass_threshold, review_threshold, False)
        reviews.extend(market_gate_reasons)
        if strategy_id == "UNCLASSIFIED": reviews.append("UNCLASSIFIED_REVIEW_ONLY")
        if pass_threshold is not None and timing_score < pass_threshold: reviews.append("ENTRY_TIMING_BELOW_STRATEGY_PASS")
        if strategy_fit < float(self.config["minimum_strategy_fit_for_pass"]): reviews.append("STRATEGY_FIT_BELOW_PASS")
        if ranking is not None and ranking < float(self.config["minimum_admission_ranking_for_pass"]): reviews.append("ADMISSION_RANKING_BELOW_PASS")
        return AdmissionV2Decision("REVIEW", _round(ranking), market_gate_status, [], list(dict.fromkeys(reviews)), pass_threshold, review_threshold, True)


def select_v2_pass(rows: list[Any], maximum: int = 20) -> list[Any]:
    return sorted(
        [row for row in rows if getattr(row, "admission_status_v2", None) == "PASS"],
        key=lambda row: (-float(getattr(row, "admission_ranking_score_v2", 0) or 0), getattr(row, "quant_rank", 999999) or 999999),
    )[:maximum]


def _round(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None
