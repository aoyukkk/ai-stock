from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from screening.exceptions import LightScreeningParseError
from screening.schemas import LightScreeningLLMOutput


VALID_DIRECTIONS = {"BUY", "WATCH", "NEUTRAL", "AVOID"}


def parse_light_screening_output(content: str) -> list[LightScreeningLLMOutput]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LightScreeningParseError("Light screening output is not valid JSON.") from exc

    items = parsed.get("items") if isinstance(parsed, dict) else None
    if not isinstance(items, list):
        raise LightScreeningParseError("Light screening output must contain items list.")

    outputs: list[LightScreeningLLMOutput] = []
    for item in items:
        if not isinstance(item, dict):
            raise LightScreeningParseError("Each light screening item must be an object.")
        outputs.append(_parse_item(item))
    return outputs


def _parse_item(item: dict[str, Any]) -> LightScreeningLLMOutput:
    required = {
        "stock_code",
        "opportunity_score",
        "event_catalyst_score",
        "sector_strength_score",
        "order_friendliness_score",
        "liquidity_score",
        "risk_penalty_score",
        "confidence",
        "direction",
        "reason",
        "risk_note",
        "should_keep",
        "data_conflict",
    }
    missing = required - set(item)
    if missing:
        raise LightScreeningParseError(f"Missing light screening fields: {sorted(missing)}")

    direction = str(item.get("direction", "NEUTRAL")).upper()
    if direction not in VALID_DIRECTIONS:
        direction = "NEUTRAL"

    return LightScreeningLLMOutput(
        stock_code=str(item["stock_code"]),
        opportunity_score=_clamp(item["opportunity_score"], 0, 100),
        event_catalyst_score=_clamp(item["event_catalyst_score"], 0, 100),
        sector_strength_score=_clamp(item["sector_strength_score"], 0, 100),
        order_friendliness_score=_clamp(item["order_friendliness_score"], 0, 100),
        liquidity_score=_clamp(item["liquidity_score"], 0, 100),
        risk_penalty_score=_clamp(item["risk_penalty_score"], 0, 100),
        confidence=_clamp(item["confidence"], 0, 1),
        direction=direction,  # type: ignore[arg-type]
        reason=str(item["reason"]),
        risk_note=str(item["risk_note"]),
        should_keep=bool(item["should_keep"]),
        data_conflict=bool(item["data_conflict"]),
    )


def _clamp(value: Any, lower: int, upper: int) -> Decimal:
    numeric = Decimal(str(value))
    return max(Decimal(lower), min(Decimal(upper), numeric)).quantize(Decimal("0.0001"))
