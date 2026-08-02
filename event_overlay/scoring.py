from __future__ import annotations

import ast
import math
from typing import Any

from event_overlay.schemas import EventReviewResult, RiskAction, ScreeningItem


def calculate_v3_screening_score(
    quant_score: float,
    review: EventReviewResult,
    weights: dict[str, float] | None = None,
    risk_action_values: dict[str, float] | None = None,
    *,
    scoring_policy: dict[str, Any] | None = None,
) -> float:
    quant_value = float(quant_score)
    if not math.isfinite(quant_value):
        raise ValueError("EVENT_OVERLAY_QUANT_SCORE_NOT_FINITE")
    if scoring_policy:
        if scoring_policy.get("mode") != "CONFIDENCE_GATED_EVENT_DELTA_V3_1":
            raise ValueError("EVENT_OVERLAY_SCORING_MODE_INVALID")
        penalties = scoring_policy.get("risk_penalties") or {}
        if review.risk_action.value not in penalties:
            raise ValueError(
                "EVENT_OVERLAY_RISK_PENALTY_MISSING:"
                + review.risk_action.value
            )
        if review.risk_action == RiskAction.BLOCK:
            return 0.0
        quant_component = max(0.0, min(100.0, quant_value))
        direction = max(-1.0, min(1.0, review.event_opportunity_score / 3.0))
        confidence = max(0.0, min(1.0, review.evidence_confidence))
        breadth = max(0.0, min(1.0, review.breadth_score))
        # Confidence and independent-source breadth qualify a single event
        # delta.  They are not separate positive score buckets, so one news
        # item cannot be rewarded three or four times.
        evidence_quality = confidence * (0.5 + 0.5 * breadth)
        event_delta = (
            float(scoring_policy["max_event_delta"])
            * direction
            * evidence_quality
        )
        if review.risk_action == RiskAction.WATCH_ONLY:
            event_delta = min(0.0, event_delta)
        risk_penalty = float(penalties[review.risk_action.value])
        value = quant_component + event_delta + risk_penalty
        return round(max(0.0, min(100.0, value)), 4)

    if weights is None or risk_action_values is None:
        raise ValueError("EVENT_OVERLAY_LEGACY_SCORE_POLICY_MISSING")
    required_weights = {
        "quant",
        "event_opportunity",
        "evidence_confidence",
        "evidence_breadth",
        "risk_action",
    }
    missing_weights = sorted(required_weights - set(weights))
    missing_risk = (
        [] if review.risk_action.value in risk_action_values
        else [review.risk_action.value]
    )
    if missing_weights or missing_risk:
        raise ValueError(
            "EVENT_OVERLAY_SCORE_COMPONENT_MISSING:"
            + ",".join(missing_weights + missing_risk)
        )
    quant_component = max(0.0, min(100.0, quant_value))
    event_component = (review.event_opportunity_score + 3.0) / 6.0 * 100.0
    evidence_component = review.evidence_confidence * 100.0
    breadth_component = review.breadth_score * 100.0
    risk_component = float(risk_action_values[review.risk_action.value])
    value = (
        weights["quant"] * quant_component
        + weights["event_opportunity"] * event_component
        + weights["evidence_confidence"] * evidence_component
        + weights["evidence_breadth"] * breadth_component
        + weights["risk_action"] * risk_component
    )
    return round(max(0.0, min(100.0, value)), 4)


def build_screening_item(
    stock: dict[str, Any],
    review: EventReviewResult,
    *,
    snapshot_id: str,
    checkpoint_status: str,
    weights: dict[str, float] | None = None,
    risk_action_values: dict[str, float] | None = None,
    scoring_policy: dict[str, Any] | None = None,
) -> ScreeningItem:
    return ScreeningItem(
        stock_code=str(stock["stock_code"]),
        stock_name=str(stock.get("stock_name") or ""),
        quant_rank=int(stock["rank"]),
        quant_score=float(stock["total_score"]),
        event_opportunity_score=review.event_opportunity_score,
        evidence_confidence=review.evidence_confidence,
        evidence_breadth=review.breadth_score,
        risk_action=review.risk_action,
        v3_screening_score=calculate_v3_screening_score(
            float(stock["total_score"]),
            review,
            weights,
            risk_action_values,
            scoring_policy=scoring_policy,
        ),
        search_status=review.search_status,
        event_snapshot_id=snapshot_id,
        checkpoint_status=checkpoint_status,
        hard_gate_reasons=_normalize_hard_gate_reasons(
            stock.get("hard_gate_reasons")
        ),
        raw_quant=dict(stock),
    )


def _normalize_hard_gate_reasons(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(reason).strip() for reason in value if str(reason).strip()]
    raw = str(value).strip()
    if not raw or raw.lower() in {"none", "null"}:
        return []
    if raw.startswith(("[", "(")):
        try:
            parsed = ast.literal_eval(raw)
        except (SyntaxError, ValueError):
            return [raw]
        if isinstance(parsed, (list, tuple, set)):
            return [
                str(reason).strip()
                for reason in parsed
                if str(reason).strip()
            ]
    return [reason.strip() for reason in raw.split(";") if reason.strip()]


def rank_items(items: list[ScreeningItem], output_top_n: int) -> list[ScreeningItem]:
    ranked = sorted(
        items,
        key=lambda item: (
            item.risk_action == RiskAction.BLOCK,
            -item.v3_screening_score,
            item.quant_rank,
            item.stock_code,
        ),
    )
    return [
        item.model_copy(
            update={
                "v3_rank": index,
                "selected_top20": index <= output_top_n and item.risk_action != RiskAction.BLOCK,
            }
        )
        for index, item in enumerate(ranked, start=1)
    ]
