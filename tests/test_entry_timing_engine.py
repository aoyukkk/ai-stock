from __future__ import annotations

from types import SimpleNamespace

from entry_timing.admission import BuyAdmissionEngine, select_admitted
from entry_timing.engine import EntryTimingEngine, EntryTimingInput


CONFIG = {
    "minimum_data_quality": 0.5,
    "admission": {
        "pass_score": 70,
        "review_score": 60,
        "minimum_quant_score": 45,
        "minimum_risk_score": 30,
        "red_market_regimes": ["BROAD_SELL_OFF", "RISK_OFF"],
        "yellow_market_regimes": ["MIXED_ROTATION", "RANGE_BOUND", "ROTATION"],
    },
    "high_position_risk": {
        "five_day_return": 0.30,
        "twenty_day_high_distance": 0.02,
        "ten_day_return": 0.50,
        "distribution_amount_ratio": 1.50,
        "distribution_max_return": 0.01,
    },
}


def _bars(closes: list[float], amounts: list[float] | None = None) -> list[dict]:
    amounts = amounts or [100_000.0] * len(closes)
    return [
        {
            "trade_date": f"2026-06-{index + 1:02d}", "adj_close": close, "close": close,
            "pre_close": closes[index - 1] if index else close, "amount": amounts[index],
        }
        for index, close in enumerate(closes)
    ]


def _input(bars, **overrides) -> EntryTimingInput:
    values = {
        "stock_code": "000001.SZ", "quant_score": 70, "quant_rank": 1, "risk_score": 70,
        "bars": bars, "daily_basic": {"turnover_rate": 5}, "moneyflow": {"net_mf_amount": 100},
        "limit_data": {}, "sector_change": 0.02, "market_regime": "BROAD_RALLY",
    }
    values.update(overrides)
    return EntryTimingInput(**values)


def test_high_position_rally_is_flagged_and_position_score_is_reduced() -> None:
    closes = [10.0] * 14 + [10.5, 11.0, 12.0, 13.0, 14.0, 15.0]
    result = EntryTimingEngine(CONFIG).evaluate(_input(_bars(closes)))
    assert "HIGH_CHASE_RISK" in result.risk_flags
    assert result.position_score <= 7


def test_healthy_low_volume_pullback_scores_above_broken_structure() -> None:
    healthy = _bars([10, 10.2, 10.5, 10.8, 11, 11.4, 11.8, 12, 11.7, 11.6, 11.55], [100] * 6 + [140, 150, 80, 70, 60])
    broken = _bars([10, 10.2, 10.5, 10.8, 11, 11.4, 11.8, 12, 11, 10.3, 9.8], [100] * 6 + [140, 150, 180, 200, 220])
    engine = EntryTimingEngine(CONFIG)
    assert engine.evaluate(_input(healthy)).pullback_score > engine.evaluate(_input(broken)).pullback_score


def test_volume_up_is_better_than_volume_down() -> None:
    base = [10.0] * 10
    up = _bars(base + [10.3], [100] * 10 + [180])
    down = _bars(base + [9.7], [100] * 10 + [180])
    engine = EntryTimingEngine(CONFIG)
    assert engine.evaluate(_input(up)).volume_price_score > engine.evaluate(_input(down)).volume_price_score


def test_sector_sync_beats_stock_rising_alone() -> None:
    bars = _bars([10.0] * 10 + [10.3])
    engine = EntryTimingEngine(CONFIG)
    synced = engine.evaluate(_input(bars, sector_change=0.02))
    alone = engine.evaluate(_input(bars, sector_change=-0.02))
    assert synced.sector_score > alone.sector_score


def test_market_green_yellow_red_scores_descend() -> None:
    bars = _bars([10.0] * 10 + [10.1])
    engine = EntryTimingEngine(CONFIG)
    green = engine.evaluate(_input(bars, market_regime="BROAD_RALLY"))
    yellow = engine.evaluate(_input(bars, market_regime="MIXED_ROTATION"))
    red = engine.evaluate(_input(bars, market_regime="BROAD_SELL_OFF"))
    assert green.market_score > yellow.market_score > red.market_score


def test_high_quant_cannot_offset_low_timing() -> None:
    item = _input(
        _bars(
            [8.0, 8.1, 8.2, 8.4, 8.8, 9.2, 9.8, 10.5, 11.5, 12.5, 12.0],
            [100] * 8 + [160, 220, 360],
        ),
        quant_score=99,
        sector_change=-0.02,
        market_regime="BROAD_SELL_OFF",
        moneyflow={"net_mf_amount": -100},
    )
    timing = EntryTimingEngine(CONFIG).evaluate(item)
    decision = BuyAdmissionEngine(CONFIG).decide(item, timing)
    assert timing.entry_timing_score < 60
    assert decision.status == "BLOCK"


def test_high_timing_with_failed_risk_gate_is_blocked() -> None:
    item = _input(_bars([10.0] * 10 + [10.2]), risk_score=10)
    timing = EntryTimingEngine(CONFIG).evaluate(item)
    decision = BuyAdmissionEngine(CONFIG).decide(item, timing)
    assert decision.status == "BLOCK"
    assert "RISK_GATE_FAILED" in decision.reasons


def test_all_gates_pass_and_zero_candidates_stays_empty() -> None:
    item = _input(_bars([9.8, 9.9, 10, 10.1, 10.2, 10.4, 10.6, 10.8, 10.5, 10.45, 10.55], [100] * 7 + [130, 80, 70, 120]))
    timing = EntryTimingEngine(CONFIG).evaluate(item)
    decision = BuyAdmissionEngine(CONFIG).decide(item, timing)
    assert decision.status == "PASS"
    assert select_admitted([], maximum_count=20) == []
    rows = [SimpleNamespace(admission_status="REVIEW", quant_rank=1)]
    assert select_admitted(rows, maximum_count=20) == []
