from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from backend.core.config_manager import ConfigManager
from stock_codes import normalize_ts_code


FLASH_SCORE_VERSION = "flash-score-v4"
FLASH_RANKING_VERSION = "flash-ranking-v4"


class FlashBatchDegenerateError(ValueError):
    def __init__(self, message: str, *, audit: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.audit = audit or {}


@dataclass(frozen=True)
class FlashScoringConfig:
    quant_consistency: Decimal = Decimal("0.30")
    fundamental_quality: Decimal = Decimal("0.25")
    financial_quality: Decimal = Decimal("0.20")
    risk_fit: Decimal = Decimal("0.15")
    data_quality: Decimal = Decimal("0.10")
    advance_threshold: Decimal = Decimal("70")
    hold_threshold: Decimal = Decimal("55")
    watch_threshold: Decimal = Decimal("40")
    version: str = FLASH_SCORE_VERSION

    def validate(self) -> None:
        total = (
            self.quant_consistency + self.fundamental_quality + self.financial_quality
            + self.risk_fit + self.data_quality
        )
        if total != Decimal("1.00"):
            raise ValueError(f"FLASH_SCORE_WEIGHT_SUM_INVALID:{total}")
        if not (self.advance_threshold > self.hold_threshold > self.watch_threshold):
            raise ValueError("FLASH_DECISION_THRESHOLDS_INVALID")


def load_flash_scoring_config(manager: ConfigManager | None = None) -> FlashScoringConfig:
    values = (manager or ConfigManager()).get_effective_config()["values"]
    config = FlashScoringConfig(
        quant_consistency=Decimal(str(values["flash_v4.weights.quant_consistency"])),
        fundamental_quality=Decimal(str(values["flash_v4.weights.fundamental_quality"])),
        financial_quality=Decimal(str(values["flash_v4.weights.financial_quality"])),
        risk_fit=Decimal(str(values["flash_v4.weights.risk_fit"])),
        data_quality=Decimal(str(values["flash_v4.weights.data_quality"])),
        advance_threshold=Decimal(str(values["flash_v4.thresholds.advance"])),
        hold_threshold=Decimal(str(values["flash_v4.thresholds.hold"])),
        watch_threshold=Decimal(str(values["flash_v4.thresholds.watch_only"])),
    )
    config.validate()
    return config


def calculate_flash_v4(
    stock_code: str,
    components: dict[str, Any],
    config: FlashScoringConfig,
    *,
    hard_risk_status: str = "NORMAL",
) -> dict[str, Any]:
    config.validate()
    fields = (
        "quant_consistency_score", "fundamental_quality_score", "financial_quality_score",
        "risk_fit_score", "data_quality_score", "data_quality_penalty", "risk_penalty",
    )
    scores = {field: Decimal(str(components[field])) for field in fields}
    for field, value in scores.items():
        if value < 0 or value > 100:
            raise ValueError(f"FLASH_COMPONENT_SCALE_INVALID:{field}")
    if Decimal("0") < scores["data_quality_score"] <= Decimal("1"):
        raise ValueError("DATA_QUALITY_SCORE_SCALE_INVALID")
    weighted = (
        scores["quant_consistency_score"] * config.quant_consistency
        + scores["fundamental_quality_score"] * config.fundamental_quality
        + scores["financial_quality_score"] * config.financial_quality
        + scores["risk_fit_score"] * config.risk_fit
        + scores["data_quality_score"] * config.data_quality
    )
    final = max(Decimal("0"), min(
        Decimal("100"), weighted - scores["data_quality_penalty"] - scores["risk_penalty"]
    )).quantize(Decimal("0.01"))
    hard_risk = str(hard_risk_status or "NORMAL").upper()
    if hard_risk in {"BLOCK", "BLOCKED", "HIGH", "HIGH_RISK", "BLACK_SWAN"}:
        decision = "BLOCK"
    elif final >= config.advance_threshold:
        decision = "ADVANCE"
    elif final >= config.hold_threshold:
        decision = "HOLD"
    elif final >= config.watch_threshold or scores["data_quality_score"] < Decimal("50"):
        decision = "WATCH_ONLY"
    else:
        decision = "REJECT"
    return {
        "stock_code": normalize_ts_code(stock_code),
        "screening_decision": decision,
        "llm_score": float(final),
        "confidence": float(Decimal(str(components["confidence"]))),
        "reason": str(components["reason"]),
        "risk_note": str(components["risk_note"]),
        "data_conflict": bool(components.get("data_conflict")),
        "requires_manual_review": True,
        "evidence_fields": list(components.get("evidence_fields") or []),
        "missing_data": list(components.get("missing_data") or []),
        **{key: float(value) for key, value in scores.items()},
        "flash_score_version": config.version,
        "ranking_version": FLASH_RANKING_VERSION,
        "scoring_formula": "LOCAL_DETERMINISTIC_WEIGHTED_V4",
    }


def assert_flash_batch_quality(rows: list[dict[str, Any]], *, canary: bool = False) -> dict[str, Any]:
    if not rows:
        raise FlashBatchDegenerateError(
            "FLASH_SCORE_DEGENERATE:EMPTY_BATCH",
            audit={"count": 0, "degenerate": True},
        )
    scores = [Decimal(str(row.get("llm_score") or 0)) for row in rows]
    decisions = [str(row.get("screening_decision") or "") for row in rows]
    confidence = [Decimal(str(row.get("confidence") or 0)) for row in rows]
    score_counts = Counter(scores)
    most_common_ratio = max(score_counts.values()) / len(scores)
    nonzero = sum(value > 0 for value in scores)
    low_score_diversity = len(score_counts) <= max(2, len(rows) // 10)
    degenerate = (
        most_common_ratio > 0.90
        or (len(set(decisions)) == 1 and low_score_diversity)
        or (len(set(confidence)) == 1 and low_score_diversity)
        or (canary and nonzero < min(3, len(rows)))
    )
    audit = {
        "count": len(rows),
        "nonzero_count": nonzero,
        "unique_score_count": len(score_counts),
        "unique_decision_count": len(set(decisions)),
        "unique_confidence_count": len(set(confidence)),
        "most_common_score_ratio": most_common_ratio,
        "low_score_diversity": low_score_diversity,
        "degenerate": degenerate,
    }
    if degenerate:
        raise FlashBatchDegenerateError("FLASH_SCORE_DEGENERATE", audit=audit)
    return audit
