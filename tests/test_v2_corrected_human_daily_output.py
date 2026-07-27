from __future__ import annotations

from datetime import date

import pytest

from scripts.build_v2_corrected_human_daily_output import FACTOR_VERSION, _payload


def _full_rows(*, constant_emotion: bool) -> list[dict]:
    return [
        {
            "rank": str(index),
            "stock_code": f"{index:06d}",
            "stock_name": f"股票{index}",
            "level_one_sector": "测试行业",
            "total_score": "75",
            "technical_score": "75",
            "capital_score": "75",
            "emotion_score": "50" if constant_emotion else str(40 + index / 10),
            "momentum_score": "75",
            "risk_score": "75",
            "hard_gate": "False",
            "factor_version": FACTOR_VERSION,
        }
        for index in range(1, 101)
    ]


def _final_audit() -> dict:
    flash = [
        {
            "stock_code": f"{index:06d}",
            "stock_name": f"股票{index}",
            "llm_score": 65,
            "screening_decision": "HOLD",
        }
        for index in range(1, 21)
    ]
    pro = [
        {
            "stock_code": f"{index:06d}",
            "stock_name": f"股票{index}",
            "pro_rank": index,
            "pro_score": 70,
            "quant_rank": index,
            "quant_score": 75,
            "flash_score": 65,
            "flash_decision": "WATCH_ONLY",
            "priority": "MEDIUM",
            "final_summary": "测试摘要",
            "key_strengths": ["测试优势"],
            "key_risks": ["测试风险"],
            "fundamental_quality": "INSUFFICIENT",
        }
        for index in range(1, 21)
    ]
    return {
        "base_run_id": "v2-recovery-20260724-123736",
        "trade_date": "2026-07-24",
        "target_trade_date": "2026-07-27",
        "final_status": "MONDAY_V2_SHADOW_FINALIZED",
        "flash": {"business_inputs": 100, "top20": flash},
        "pro": {"results": pro},
        "watch_pool": [],
        "active_shadow": [],
        "order_plans": [],
    }


def test_v2_payload_uses_differentiated_emotion_and_model_top20() -> None:
    payload, audit = _payload(
        date(2026, 7, 24),
        _full_rows(constant_emotion=False),
        _final_audit(),
        [],
    )

    assert audit["top100_emotion_unique_count"] == 100
    assert audit["top100_emotion_all_50"] is False
    assert audit["model_top20_count"] == 20
    assert len(payload["quant_top100"]) == 100
    assert len(payload["candidates"]) == 20


def test_v2_payload_rejects_legacy_constant_emotion() -> None:
    with pytest.raises(RuntimeError, match="V2_EMOTION_SCORE_NOT_DIFFERENTIATED"):
        _payload(
            date(2026, 7, 24),
            _full_rows(constant_emotion=True),
            _final_audit(),
            [],
        )
