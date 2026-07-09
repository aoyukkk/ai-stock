from decimal import Decimal

from screening.prompt_builder import build_light_screening_prompt
from screening.schemas import LightScreeningInput


def make_input() -> LightScreeningInput:
    return LightScreeningInput(
        stock_code="000001",
        stock_name="平安银行",
        industry="银行",
        quant_rank=1,
        quant_total_score=Decimal("80"),
        technical_score=Decimal("75"),
        capital_score=Decimal("82"),
        emotion_score=Decimal("70"),
        momentum_score=Decimal("68"),
        risk_score=Decimal("88"),
        quant_reason="compressed quant reason",
        latest_news_summary="mock news",
        overseas_summary="mock overseas",
        liquidity_summary="mock liquidity",
    )


def test_prompt_contains_compressed_summary_and_json_schema() -> None:
    prompt = build_light_screening_prompt([make_input()])

    assert "000001" in prompt
    assert "quant_total_score" in prompt
    assert "Return strict JSON" in prompt
    assert "opportunity_score" in prompt
    assert "should_keep" in prompt


def test_prompt_does_not_include_full_kline_or_forbidden_requests() -> None:
    prompt = build_light_screening_prompt([make_input()]).lower()

    assert "kline_bars" not in prompt
    assert "ohlcv" not in prompt
    assert "recommended_price" not in prompt
    assert "stop_loss_price" not in prompt
    assert "take_profit" not in prompt
    assert "do not generate order prices" in prompt
    assert "real-money trading commands" in prompt
    assert "not a real trading instruction" in prompt
