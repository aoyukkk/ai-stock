from llm_gateway.pricing import apply_configured_cost
from llm_gateway.schemas import LLMResponse


def _response(model="deepseek-v4-flash"):
    return LLMResponse(
        provider="deepseek",
        model=model,
        content="{}",
        input_tokens=100,
        input_cache_hit_tokens=40,
        input_cache_miss_tokens=60,
        output_tokens=20,
        total_tokens=120,
        latency_ms=1,
        request_hash="hash",
        status="ok",
    )


def test_cost_uses_separate_cache_hit_miss_and_output_prices() -> None:
    pricing = {
        "deepseek": {
            "deepseek-v4-flash": {
                "input_cache_hit_per_million": 1,
                "input_cache_miss_per_million": 2,
                "output_per_million": 3,
                "currency": "USD",
                "version": "fixture-v1",
            }
        }
    }
    response = apply_configured_cost(_response(), pricing)
    assert response.cost_usd == 0.00022
    assert response.cost_status == "CALCULATED"
    assert response.pricing_version == "fixture-v1"


def test_missing_price_keeps_cost_null() -> None:
    response = apply_configured_cost(_response(), {})
    assert response.cost_usd is None
    assert response.cost_status == "COST_NOT_CONFIGURED"
