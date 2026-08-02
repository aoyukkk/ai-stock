from __future__ import annotations

import json

import scripts.build_human_daily_output as human_daily
from scripts.build_human_daily_output import (
    _apply_verification_overlay,
    _is_reportable_order_warning,
    _load_verification_overlays,
)


def test_verified_fundamental_replaces_failed_text_without_special_marker():
    candidate = {"产业链": "信息不足*", "核心逻辑": "分析失败*", "主要风险": "信息不足"}
    fundamental = {
        "产业链": "信息不足*",
        "链条位置": "信息不足*",
        "主营业务": "分析失败*",
        "核心产品": "分析失败*",
        "潜在优势": "分析失败*",
        "人工复核": "是",
    }
    verification = {
        "business": "主营空调风叶、机械风机和高分子复合材料。",
        "industry_chain": "暖通空调零部件产业链，中游制造。",
        "conclusion": "传统主业稳定，增量业务仍需业绩验证。",
        "risks": "行业竞争和原材料波动。",
        "source_url": "https://example.com/annual-report",
    }

    _apply_verification_overlay(candidate, fundamental, verification)

    assert candidate["核心逻辑"] == verification["conclusion"]
    assert fundamental["主营业务"] == verification["business"]
    assert fundamental["链条位置"] == "中游"
    assert fundamental["人工复核"] == "已联网核验"
    assert fundamental["核验来源"] == verification["source_url"]
    assert "*" not in "".join(str(value) for value in fundamental.values())


def test_expected_zero_position_warning_is_not_an_unresolved_issue():
    row = {
        "stock_code": "300145",
        "reason": "LLM_UNVERIFIED_POSITION_DISCOUNT_APPLIED；人工选择，但LLM未入选",
    }

    assert not _is_reportable_order_warning(row, set())
    assert not _is_reportable_order_warning(
        {"stock_code": "603726", "reason": "人工选择，但LLM分析失败"},
        {"603726"},
    )


def test_verification_overlay_is_bound_to_exact_date_and_run(tmp_path, monkeypatch):
    monkeypatch.setattr(human_daily, "ROOT", tmp_path)
    directory = tmp_path / "outputs" / "2026-07-27" / "人工核验"
    directory.mkdir(parents=True)
    base = {
        "artifact_type": "HUMAN_VERIFICATION_OVERLAY_V1",
        "trade_date": "2026-07-27",
        "reviewed_at": "2026-07-27T17:30:00+08:00",
        "items": [{
            "stock_code": "000001",
            "status": "已联网核验",
            "source_url": "https://example.com/report",
        }],
    }
    (directory / "stale.json").write_text(
        json.dumps({**base, "validation_run_id": "other-run"}),
        encoding="utf-8",
    )
    (directory / "current.json").write_text(
        json.dumps({**base, "validation_run_id": "current-run"}),
        encoding="utf-8",
    )

    assert _load_verification_overlays("2026-07-27", "current-run") == {
        "000001": {
            "stock_code": "000001",
            "status": "已联网核验",
            "source_url": "https://example.com/report",
        }
    }
