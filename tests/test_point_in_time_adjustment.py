from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from datasource.schemas import KlineBar
from quant.price_adjustment import QFQ_POINT_IN_TIME, RAW, build_adjusted_price_series


SHANGHAI = ZoneInfo("Asia/Shanghai")


def _bars() -> list[KlineBar]:
    values = [(date(2026, 1, 5), "10", "9.5"), (date(2026, 1, 6), "5", "10")]
    return [
        KlineBar(
            stock_code="000001",
            trade_date=trade_date,
            open=Decimal(close),
            high=Decimal(close) + Decimal("0.2"),
            low=Decimal(close) - Decimal("0.2"),
            close=Decimal(close),
            pre_close=Decimal(pre_close),
            volume=100,
            amount=Decimal("1000"),
        )
        for trade_date, close, pre_close in values
    ]


def test_qfq_uses_fixed_end_date_anchor_and_adjusts_all_ohlc_fields() -> None:
    result = build_adjusted_price_series(
        _bars(),
        {
            "20260105": {"adj_factor": "1"},
            "20260106": {"adj_factor": "2"},
        },
        mode=QFQ_POINT_IN_TIME,
        decision_time=datetime(2026, 1, 8, 9, tzinfo=SHANGHAI),
        base_market_trade_date=date(2026, 1, 6),
    )

    assert result.active is True
    assert result.adjustment_anchor_date == date(2026, 1, 6)
    assert result.bars[0].close == Decimal("5.0000")
    assert result.bars[0].high == Decimal("5.1000")
    assert result.bars[1].close == Decimal("5.0000")


def test_future_or_not_yet_available_factor_falls_back_to_raw() -> None:
    bars = _bars()
    result = build_adjusted_price_series(
        bars,
        {"20260106": {"adj_factor": "2"}},
        mode=QFQ_POINT_IN_TIME,
        decision_time=datetime(2026, 1, 6, 20, tzinfo=SHANGHAI),
        base_market_trade_date=date(2026, 1, 6),
    )

    assert result.technical_price_basis == RAW
    assert result.warning == "POINT_IN_TIME_ADJUSTMENT_UNAVAILABLE"
    assert result.bars == bars
