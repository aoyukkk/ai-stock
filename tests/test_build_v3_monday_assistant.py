from __future__ import annotations

from scripts.build_v3_monday_assistant import (
    _core_event,
    _event_summary,
    _risk_summary,
)


def _event(
    title: str,
    *,
    eligible: bool | None,
    direction: str = "POSITIVE",
    materiality: float = 0.5,
) -> dict:
    row = {
        "title": title,
        "summary": f"{title}摘要",
        "event_direction": direction,
        "materiality": materiality,
        "relevance": 1.0,
        "confidence": 1.0,
    }
    if eligible is not None:
        row["score_eligible"] = eligible
    return row


def test_v31_workbook_core_event_excludes_ineligible_evidence():
    snapshot = {
        "items": [
            _event("过期高权重消息", eligible=False, materiality=1.0),
            _event("最新合格消息", eligible=True, materiality=0.4),
        ]
    }

    assert _core_event(snapshot)["title"] == "最新合格消息"
    assert "过期高权重消息" not in _event_summary(snapshot)


def test_v31_workbook_no_eligible_evidence_keeps_neutral_summary():
    snapshot = {
        "items": [
            _event("不得展示的旧消息", eligible=False, materiality=1.0),
        ]
    }

    assert _core_event(snapshot) == {}
    assert _event_summary(snapshot) == (
        "V3.1事件层未发现合格的新消息，保持Quant原分并保留基本面复核。"
    )


def test_v31_workbook_risk_summary_excludes_ineligible_negative_evidence():
    snapshot = {
        "items": [
            _event(
                "过期负面消息",
                eligible=False,
                direction="NEGATIVE",
                materiality=1.0,
            ),
        ]
    }

    summary = _risk_summary(snapshot, "KEEP")
    assert "过期负面消息" not in summary
    assert "开盘前仍需核验" in summary


def test_legacy_v3_snapshot_without_eligibility_field_remains_compatible():
    snapshot = {
        "items": [
            _event("旧版低权重", eligible=None, materiality=0.2),
            _event("旧版高权重", eligible=None, materiality=0.8),
        ]
    }

    assert _core_event(snapshot)["title"] == "旧版高权重"
