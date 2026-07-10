from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from llm_gateway.schemas import LLMResponse


def apply_configured_cost(response: LLMResponse, pricing: dict[str, Any]) -> LLMResponse:
    if response.provider == "mock":
        response.cost_usd = 0.0
        response.cost_status = "CALCULATED"
        response.pricing_version = "mock-zero-cost"
        return response
    provider_prices = pricing.get(response.provider, {}) if isinstance(pricing, dict) else {}
    model_price = provider_prices.get(response.model, {}) if isinstance(provider_prices, dict) else {}
    required = {"input_cache_hit_per_million", "input_cache_miss_per_million", "output_per_million"}
    if not isinstance(model_price, dict) or not required.issubset(model_price):
        response.cost_usd = None
        response.cost_status = "COST_NOT_CONFIGURED"
        return response

    currency = str(model_price.get("currency") or "").upper()
    if currency != "USD":
        response.cost_usd = None
        response.cost_status = "COST_CURRENCY_UNSUPPORTED"
        response.pricing_version = str(model_price.get("version") or "") or None
        return response

    hit = Decimal(response.input_cache_hit_tokens)
    miss = Decimal(response.input_cache_miss_tokens)
    output = Decimal(response.output_tokens)
    per_million = Decimal("1000000")
    cost = (
        hit * Decimal(str(model_price["input_cache_hit_per_million"]))
        + miss * Decimal(str(model_price["input_cache_miss_per_million"]))
        + output * Decimal(str(model_price["output_per_million"]))
    ) / per_million
    response.cost_usd = float(cost.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP))
    response.cost_status = "CALCULATED"
    response.pricing_version = str(model_price.get("version") or "") or None
    return response
