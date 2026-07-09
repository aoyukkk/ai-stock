from __future__ import annotations


MODEL_PRICE_PER_1K_TOKENS: dict[str, float] = {
    "mock-chat": 0.0,
    "mock-fast": 0.0,
    "mock-reasoning": 0.0,
}


def estimate_cost_usd(provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
    if provider == "mock":
        return 0.0
    price = MODEL_PRICE_PER_1K_TOKENS.get(model, 0.0)
    return round((input_tokens + output_tokens) / 1000 * price, 6)
