from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from entry_timing.engine import EntryTimingAssessment, EntryTimingInput


@dataclass(frozen=True)
class AdmissionDecision:
    status: str
    reasons: list[str]


class BuyAdmissionEngine:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def decide(self, item: EntryTimingInput, timing: EntryTimingAssessment) -> AdmissionDecision:
        cfg = self.config.get("admission") or {}
        if timing.timing_status == "DATA_INSUFFICIENT":
            return AdmissionDecision("DATA_INSUFFICIENT", ["DATA_QUALITY_BELOW_THRESHOLD"])
        reasons: list[str] = []
        if item.quant_score < float(cfg.get("minimum_quant_score", 45)):
            reasons.append("QUANT_SCORE_BELOW_THRESHOLD")
        if timing.entry_timing_score < float(cfg.get("review_score", 60)):
            reasons.append("ENTRY_TIMING_BELOW_REVIEW_THRESHOLD")
        if item.risk_score is not None and item.risk_score < float(cfg.get("minimum_risk_score", 30)):
            reasons.append("RISK_GATE_FAILED")
        if reasons:
            return AdmissionDecision("BLOCK", reasons)
        capped_to_review = bool(timing.risk_flags) or item.market_regime in set(cfg.get("red_market_regimes") or [])
        if timing.entry_timing_score >= float(cfg.get("pass_score", 70)) and not capped_to_review:
            return AdmissionDecision("PASS", [])
        review_reasons = list(timing.risk_flags)
        if item.market_regime in set(cfg.get("red_market_regimes") or []):
            review_reasons.append("MARKET_GATE_REVIEW")
        if not review_reasons:
            review_reasons.append("ENTRY_TIMING_REVIEW_BAND")
        return AdmissionDecision("REVIEW", review_reasons)


def select_admitted(rows: list[Any], maximum_count: int = 20) -> list[Any]:
    """Return PASS rows only; never relax the gate to fill a fixed count."""
    passed = [row for row in rows if getattr(row, "admission_status", None) == "PASS"]
    return sorted(passed, key=lambda row: (getattr(row, "quant_rank", None) is None, getattr(row, "quant_rank", 999999)))[:maximum_count]
