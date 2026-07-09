from __future__ import annotations

from typing import Any

from screening.schemas import LightScreeningResult
from agents.schemas import CommitteeInput


FORBIDDEN_CONTEXT_KEYS = {
    "kline_bars",
    "ohlcv",
    "api_key",
    "password",
    "secret",
    "token",
    "username",
    "recommended_price",
    "stop_loss_price",
    "take_profit",
    "order_price",
    "trade_order",
}


def build_committee_input(result: LightScreeningResult) -> CommitteeInput:
    return CommitteeInput(
        stock_code=result.stock_code,
        stock_name=result.stock_name,
        industry=result.industry,
        quant_rank=result.quant_rank,
        quant_total_score=result.quant_total_score,
        light_rank=int(result.rank or 0),
        final_light_score=result.final_light_score,
        light_direction=result.direction,
        light_reason=_clean_text(result.reason),
        technical_summary=(
            f"Compressed technical view: quant rank {result.quant_rank}, "
            f"quant score {result.quant_total_score}, light score {result.final_light_score}."
        ),
        capital_summary=(
            f"Compressed capital view: light confidence {result.confidence}, "
            "liquidity was reviewed by the light screening step."
        ),
        emotion_summary=(
            f"Compressed emotion view: light direction {result.direction}, "
            "sector and market heat are represented only as summary text."
        ),
        news_summary="Mock news summary only; no real news source was queried.",
        overseas_summary="Mock overseas summary only; no real overseas provider was queried.",
        risk_summary=_clean_text(result.risk_note),
    )


def committee_input_to_prompt_context(context: CommitteeInput) -> dict[str, Any]:
    payload = context.model_dump(mode="json")
    return {
        key: _clean_text(value) if isinstance(value, str) else value
        for key, value in payload.items()
        if key.lower() not in FORBIDDEN_CONTEXT_KEYS
    }


def _clean_text(value: str | None) -> str:
    text = (value or "").replace("\r", " ").replace("\n", " ").strip()
    for forbidden in FORBIDDEN_CONTEXT_KEYS:
        text = text.replace(forbidden, "[removed]")
    return text[:480]
