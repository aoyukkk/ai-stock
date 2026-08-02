from __future__ import annotations

from datetime import date

import pytest

from scripts.build_v2_corrected_human_daily_output import FACTOR_VERSION, _payload
from scripts.run_tushare_quant_v2_validation import classify_universe_exclusions


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
        "flash": {
            "business_inputs": 20,
            "top20": flash,
            "audit": [
                {"stock_code": f"{index:06d}"}
                for index in range(1, 21)
            ],
        },
        "pro": {
            "business_inputs": 20,
            "successful": 20,
            "failed": 0,
            "results": pro,
        },
        "market_regime": {"regime": "RISK_ON"},
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
    assert audit["market_regime"] == "RISK_ON"
    assert payload["quant_top100"][0]["进入二筛"] == "是"
    assert payload["quant_top100"][20]["进入二筛"] == "否"


def test_v2_payload_rejects_legacy_constant_emotion() -> None:
    with pytest.raises(RuntimeError, match="V2_EMOTION_SCORE_NOT_DIFFERENTIATED"):
        _payload(
            date(2026, 7, 24),
            _full_rows(constant_emotion=True),
            _final_audit(),
            [],
        )


def test_v2_payload_keeps_flash_top20_when_one_pro_review_fails() -> None:
    audit_data = _final_audit()
    audit_data["pro"]["results"] = audit_data["pro"]["results"][:-1]
    audit_data["pro"]["successful"] = 19
    audit_data["pro"]["failed"] = 1
    audit_data["watch_pool"] = [{"stock_code": "000001"}]

    payload, audit = _payload(
        date(2026, 7, 24),
        _full_rows(constant_emotion=False),
        audit_data,
        [],
    )

    failed = next(
        row for row in payload["candidates"] if row["股票代码"] == "000020"
    )
    issue = next(row for row in payload["issues"] if row["股票代码"] == "000020")
    assert len(payload["candidates"]) == 20
    assert failed["当前状态"] == "Pro复核失败，仅观察"
    assert failed["建议仓位"] == 0
    watched = next(row for row in payload["orders"] if row["股票代码"] == "000001")
    assert watched["说明"].startswith("RISK_ON")
    assert issue["当前状态"] == "已降级、不可部署"
    assert audit["model_top20_count"] == 20
    assert audit["pro_successful_count"] == 19
    assert audit["pro_failed_count"] == 1


def test_v2_payload_reports_current_refill_network_calls_not_reused_rows() -> None:
    _, audit = _payload(
        date(2026, 7, 24),
        _full_rows(constant_emotion=False),
        _final_audit(),
        [],
        fundamental_actual_network_calls=1,
    )

    assert audit["fundamental_llm_actual_network_calls"] == 1


def test_unscored_new_listing_is_explained_in_current_issues() -> None:
    exclusions = classify_universe_exclusions(
        {"000001", "688825"},
        {"000001"},
        histories={"000001": [{}] * 30, "688825": [{}]},
        stock_by_code={
            "688825": {
                "name": "长鑫科技",
                "list_date": "20260727",
            }
        },
    )

    payload, _ = _payload(
        date(2026, 7, 24),
        _full_rows(constant_emotion=False),
        _final_audit(),
        [],
        universe_exclusions=exclusions,
    )

    issue = next(row for row in payload["issues"] if row["股票代码"] == "688825")
    assert issue["当前状态"] == "未评分、等待数据成熟"
    assert "DATA_INSUFFICIENT_NEW_LISTING" in issue["说明"]


def test_st_universe_exclusion_is_labeled_as_hard_gate() -> None:
    payload, _ = _payload(
        date(2026, 7, 24),
        _full_rows(constant_emotion=False),
        _final_audit(),
        [],
        universe_exclusions=[
            {
                "stock_code": "000010",
                "stock_name": "*ST样本",
                "list_date": "19951027",
                "history_count": 101,
                "reason": "BASELINE_UNIVERSE_OMISSION",
            }
        ],
    )

    issue = next(row for row in payload["issues"] if row["股票代码"] == "000010")
    assert issue["问题类型"] == "V2硬门禁"
    assert issue["当前状态"] == "已阻断、保留审计"
    assert "历史不足20个交易日" not in issue["说明"]
