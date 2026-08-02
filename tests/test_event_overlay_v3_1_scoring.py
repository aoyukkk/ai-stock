from __future__ import annotations

from pathlib import Path

import pytest

from event_overlay.config import load_event_overlay_config
from event_overlay.schemas import EventReviewResult, RiskAction
from event_overlay.scoring import build_screening_item, calculate_v3_screening_score


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "event_overlay_v3_1.yaml"


def _review(
    *,
    opportunity: float = 0.0,
    confidence: float = 0.0,
    breadth: float = 0.0,
    risk: RiskAction = RiskAction.KEEP,
) -> EventReviewResult:
    return EventReviewResult(
        stock_code="000001",
        search_status="NO_MATERIAL_EVENT",
        material_events=[],
        event_opportunity_score=opportunity,
        evidence_confidence=confidence,
        breadth_score=breadth,
        risk_action=risk,
    )


def _policy() -> dict:
    return load_event_overlay_config(CONFIG)["scoring"]


def test_v3_1_no_fresh_event_keeps_quant_score_unchanged() -> None:
    assert calculate_v3_screening_score(
        82.5,
        _review(),
        scoring_policy=_policy(),
    ) == 82.5


def test_v3_1_positive_event_is_one_confidence_gated_delta() -> None:
    score = calculate_v3_screening_score(
        80.0,
        _review(opportunity=3.0, confidence=0.75, breadth=1.0, risk=RiskAction.PROMOTE),
        scoring_policy=_policy(),
    )
    assert score == pytest.approx(86.0)
    assert score <= 88.0


def test_v3_1_low_breadth_reduces_same_event_delta() -> None:
    broad = calculate_v3_screening_score(
        80.0,
        _review(opportunity=2.0, confidence=0.7, breadth=1.0),
        scoring_policy=_policy(),
    )
    narrow = calculate_v3_screening_score(
        80.0,
        _review(opportunity=2.0, confidence=0.7, breadth=0.0),
        scoring_policy=_policy(),
    )
    assert narrow < broad


def test_v3_1_watch_only_is_penalty_not_positive_bucket() -> None:
    keep = calculate_v3_screening_score(
        80.0,
        _review(opportunity=-1.0, confidence=0.7, breadth=1.0),
        scoring_policy=_policy(),
    )
    watch = calculate_v3_screening_score(
        80.0,
        _review(
            opportunity=-1.0,
            confidence=0.7,
            breadth=1.0,
            risk=RiskAction.WATCH_ONLY,
        ),
        scoring_policy=_policy(),
    )
    assert watch == pytest.approx(keep - 4.0)


def test_v3_1_watch_only_can_never_gain_from_net_positive_conflict() -> None:
    score = calculate_v3_screening_score(
        80.0,
        _review(
            opportunity=2.5,
            confidence=1.0,
            breadth=1.0,
            risk=RiskAction.WATCH_ONLY,
        ),
        scoring_policy=_policy(),
    )
    assert score == 76.0
    assert score < 80.0


def test_v3_1_block_score_is_zero() -> None:
    assert calculate_v3_screening_score(
        99.0,
        _review(opportunity=-3.0, confidence=1.0, breadth=1.0, risk=RiskAction.BLOCK),
        scoring_policy=_policy(),
    ) == 0.0


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_v3_1_rejects_non_finite_quant_score(invalid) -> None:
    with pytest.raises(ValueError, match="QUANT_SCORE_NOT_FINITE"):
        calculate_v3_screening_score(
            invalid,
            _review(),
            scoring_policy=_policy(),
        )


def test_v3_1_config_remains_shadow_and_has_strict_freshness() -> None:
    config = load_event_overlay_config(CONFIG)
    assert config["production_or_shadow"] == "SHADOW"
    assert config["freshness"] == {
        "default_hours": 72,
        "announcement_hours": 168,
        "market_news_hours": 36,
        "unknown_publish_time_eligible": False,
        "announcement_source_types": ["交易所公告", "公司公告", "政府公告", "定期报告", "临时公告"],
        "market_news_source_types": ["市场新闻", "媒体报道", "行业新闻"],
    }


@pytest.mark.parametrize("raw_reasons", [[], "[]", "", None])
def test_v3_1_empty_hard_gate_reasons_remain_empty(raw_reasons) -> None:
    item = build_screening_item(
        {
            "stock_code": "000001",
            "stock_name": "平安银行",
            "rank": 1,
            "total_score": 80.0,
            "hard_gate_reasons": raw_reasons,
        },
        _review(),
        snapshot_id="snapshot",
        checkpoint_status="NEW",
        scoring_policy=_policy(),
    )
    assert item.hard_gate_reasons == []


def test_v3_1_nonempty_hard_gate_reasons_are_preserved() -> None:
    item = build_screening_item(
        {
            "stock_code": "000001",
            "stock_name": "平安银行",
            "rank": 1,
            "total_score": 80.0,
            "hard_gate_reasons": ["ST", "SUSPENDED"],
        },
        _review(),
        snapshot_id="snapshot",
        checkpoint_status="NEW",
        scoring_policy=_policy(),
    )
    assert item.hard_gate_reasons == ["ST", "SUSPENDED"]
