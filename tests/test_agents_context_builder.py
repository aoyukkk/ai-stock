from decimal import Decimal

from agents.context_builder import build_committee_input, committee_input_to_prompt_context
from screening.schemas import LightScreeningResult


def make_light_result() -> LightScreeningResult:
    return LightScreeningResult(
        stock_code="000001",
        stock_name="Ping An Bank",
        industry="bank",
        quant_rank=1,
        quant_total_score=Decimal("72"),
        llm_score=Decimal("70"),
        final_light_score=Decimal("68"),
        rank=1,
        direction="WATCH",
        confidence=Decimal("0.72"),
        should_keep=True,
        reason="compressed light reason",
        risk_note="compressed risk note",
        prompt_version="v0.3-phase6",
        model_name="mock-chat",
        request_hash="hash",
    )


def test_build_committee_input_from_light_screening_result() -> None:
    context = build_committee_input(make_light_result())

    assert context.stock_code == "000001"
    assert context.quant_rank == 1
    assert context.light_rank == 1
    assert context.final_light_score == Decimal("68")
    assert context.technical_summary
    assert context.risk_summary == "compressed risk note"


def test_committee_prompt_context_is_compressed_and_safe() -> None:
    prompt_context = committee_input_to_prompt_context(build_committee_input(make_light_result()))
    text = str(prompt_context).lower()

    assert "kline_bars" not in text
    assert "ohlcv" not in text
    assert "api_key" not in text
    assert "password" not in text
    assert "secret" not in text
    assert "token" not in text
    assert "real trading instruction" not in text
    assert "trade_order" not in text
    assert "recommended_price" not in text
