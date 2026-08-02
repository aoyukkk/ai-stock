from __future__ import annotations

from quant.shadow.risk_v2_1 import RiskV21Input, calculate_risk_v2_1
from scripts.freeze_monday_v2_decision import _layer_rows, audit_checkpoint


def test_risk_v2_1_direction_and_missing_policy() -> None:
    safe = calculate_risk_v2_1(
        RiskV21Input(
            risk_v2_frozen=80,
            max_drawdown_20d=5,
            downside_volatility_20d=1,
            chip_profit_ratio=55,
            close_to_chip_cost=1.02,
            unlock_risk=5,
            reduction_risk=5,
            pledge_risk=5,
            major_financial_event_risk=5,
        )
    )
    risky = calculate_risk_v2_1(
        RiskV21Input(
            risk_v2_frozen=80,
            max_drawdown_20d=25,
            downside_volatility_20d=7,
            chip_profit_ratio=92,
            close_to_chip_cost=1.30,
            unlock_risk=80,
            reduction_risk=10,
            pledge_risk=10,
            major_financial_event_risk=10,
        )
    )
    missing = calculate_risk_v2_1(
        RiskV21Input(
            risk_v2_frozen=80,
            max_drawdown_20d=None,
            downside_volatility_20d=None,
            chip_profit_ratio=None,
            close_to_chip_cost=None,
            unlock_risk=None,
            reduction_risk=None,
            pledge_risk=None,
            major_financial_event_risk=None,
        )
    )
    assert safe["score"] > risky["score"]
    assert missing["score"] == 32.0
    assert missing["coverage"] == 0.4
    assert len(missing["missing_components"]) == 4


def test_risk_v2_1_pipeline_error_has_no_score() -> None:
    result = calculate_risk_v2_1(
        RiskV21Input(
            risk_v2_frozen=80,
            max_drawdown_20d=5,
            downside_volatility_20d=1,
            chip_profit_ratio=55,
            close_to_chip_cost=1.02,
            unlock_risk=5,
            reduction_risk=5,
            pledge_risk=5,
            major_financial_event_risk=5,
            pipeline_error="JOIN_FAILED",
        )
    )
    assert result["status"] == "DATA_PIPELINE_ERROR"
    assert result["score"] is None


def test_four_layer_sample_counts_from_frozen_sources() -> None:
    from scripts.freeze_monday_v2_decision import _load_sources

    sources = _load_sources()
    rows = _layer_rows(sources)
    assert sum(row["layer"] == "ACTIVE_SHADOW" for row in rows) == 2
    assert sum(row["layer"] == "WATCH_POOL" for row in rows) == 10
    assert sum(row["layer"] == "FLASH_TOP20" for row in rows) == 20
    assert sum(row["layer"] == "V2_TOP100" for row in rows) == 100


def test_checkpoint_hashes_match_without_llm_calls() -> None:
    from scripts.freeze_monday_v2_decision import _load_sources

    audit = audit_checkpoint(_load_sources())
    assert audit["logical_evaluations"] == 106
    assert audit["actual_network_calls"] == 106
    assert audit["reused_checkpoint_count"] == 106
    assert audit["reused_input_hash_match"] == 106
    assert audit["stale_checkpoint_count"] == 0
