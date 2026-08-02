from __future__ import annotations

import hashlib
import inspect
import json
from types import SimpleNamespace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import httpx
from fastapi import HTTPException
from openpyxl import load_workbook
import event_overlay.service as event_overlay_service

from backend.api.event_overlay import _resolve_run
from event_overlay.checkpoint import (
    CheckpointStore,
    EventOverlayCheckpointContract,
    exclusive_file_lock,
)
from event_overlay.config import ROOT, load_event_overlay_config
from event_overlay.constants import (
    DECISION_VERSION,
    DIRECT_SEARCH_FALLBACK,
    EVENT_REVIEW_VERSION,
    PRODUCTION_OR_SHADOW,
    SCREENING_VERSION,
    SEARCH_CONTRACT_VERSION,
)
from event_overlay.evidence import (
    normalize_search_result,
    novelty_score,
    reaction_adjusted_opportunity,
)
from event_overlay.exporter import (
    REQUIRED_SHEETS,
    _cell_value,
    export_run,
    validate_workbook,
)
from event_overlay.forward_ab import (
    EXECUTION_CONTRACT,
    apply_shadow_deployment_limits,
    build_forward_ab_manifest,
    promote_demote_counterfactual,
)
from event_overlay.provider import (
    DeepSeekFlashDirectSearchProvider,
    MockEventSearchProvider,
    NoNetworkEventSearchProvider,
    _sanitize_event_rows,
)
from event_overlay.schemas import EventReviewResult, RiskAction
from event_overlay.scoring import build_screening_item, rank_items
from event_overlay.service import EventOverlayShadowService, RunOptions
from llm_gateway.schemas import LLMResponse
from scripts import run_v3_event_overlay_shadow_once as v3_runner


NOW = datetime(2026, 7, 30, 15, 30, tzinfo=timezone(timedelta(hours=8)))
STOCK = {
    "stock_code": "000001",
    "stock_name": "平安银行",
    "rank": 1,
    "total_score": 76.5,
    "hard_gate_reasons": "",
}


def _raw(
    direction: str = "POSITIVE",
    *,
    url: str | None = "https://example.com/a",
    tier: str = "tier_2",
    materiality: float = 0.8,
    published_at: datetime | None = NOW - timedelta(hours=2),
    title: str = "事件A",
    domain_suffix: str = "a",
    source_type: str = "ANNOUNCEMENT",
    event_type: str = "ORDER",
) -> dict:
    return {
        "status": "VERIFIED_WITH_STRUCTURED_PROVIDER",
        "provider": "mock",
        "provider_verified": True,
        "direct_search_used": False,
        "items": [{
            "event_type": event_type,
            "title": title,
            "summary": "可审计事件",
            "url": url and url.replace("/a", f"/{domain_suffix}"),
            "published_at": published_at.isoformat() if published_at else None,
            "source_tier": tier,
            "source_type": source_type,
            "event_direction": direction,
            "materiality": materiality,
            "relevance": 0.9,
            "confidence": 0.9,
        }],
    }


def _review(raw: dict | None = None) -> EventReviewResult:
    return normalize_search_result(
        STOCK,
        raw or _raw(),
        decision_as_of_time=NOW,
        max_events=5,
        confidence_discount=0.7,
    )


def _contract(**updates) -> EventOverlayCheckpointContract:
    payload = {
        "trade_date": "2026-07-30",
        "stock_code": "000001",
        "factor_version": "TUSHARE_QUANT_V2_CORRECTED_SHADOW",
        "input_hash": "a" * 64,
        "prompt_version": "P1",
        "prompt_hash": "b" * 64,
        "schema_version": EVENT_REVIEW_VERSION,
        "contract_version": SEARCH_CONTRACT_VERSION,
        "data_manifest_hash": "c" * 64,
        "membership_manifest_hash": "d" * 64,
        "fundamental_snapshot_hash": "e" * 64,
        "news_snapshot_hash": "f" * 64,
        "overseas_snapshot_hash": "1" * 64,
        "market_regime_hash": "2" * 64,
        "risk_version": "RISK_V2_1_SHADOW",
        "decision_as_of_time": NOW,
        "search_mode": "MOCK_SEARCH",
        "search_query_hash": "3" * 64,
        "screening_config_hash": "4" * 64,
        "source_tier_policy_hash": "5" * 64,
    }
    payload.update(updates)
    return EventOverlayCheckpointContract(**payload)


@pytest.fixture(scope="module")
def workbook_artifact(tmp_path_factory):
    root = tmp_path_factory.mktemp("v3-export")
    review = _review()
    item = build_screening_item(
        STOCK,
        review,
        snapshot_id="snapshot-1",
        checkpoint_status="NEW",
        weights=load_event_overlay_config()["weights"],
        risk_action_values=load_event_overlay_config()["risk_actions"],
    ).model_copy(update={"v3_rank": 1, "selected_top20": True}).model_dump(mode="json")
    snapshot = {
        "snapshot_id": "snapshot-1",
        "run_id": "run-1",
        "trade_date": "2026-07-30",
        "decision_as_of_time": NOW.isoformat(),
        "stock_code": "000001",
        "query": "query",
        "search_status": review.search_status,
        "provider": "mock",
        "provider_verified": True,
        "direct_search_used": False,
        "confidence_discount_applied": False,
        "production_eligible": False,
        "shadow_eligible": True,
        "items": [event.model_dump(mode="json") for event in review.material_events],
        "input_hash": "a" * 64,
        "content_hash": "b" * 64,
        "contract_version": SEARCH_CONTRACT_VERSION,
    }
    manifest = {
        "run_id": "run-1",
        "trade_date": "2026-07-30",
        "decision_as_of_time": NOW.isoformat(),
        "source_run_id": "v2-run",
        "source_input_hash": "a" * 64,
        "universe_snapshot_id": "universe-1",
        "factor_version": "TUSHARE_QUANT_V2_CORRECTED_SHADOW",
        "screening_version": SCREENING_VERSION,
        "decision_version": DECISION_VERSION,
        "event_review_version": EVENT_REVIEW_VERSION,
        "search_contract_version": SEARCH_CONTRACT_VERSION,
        "production_or_shadow": "SHADOW",
        "execution_mode": "MOCK_SEARCH",
        "real_search_enabled": False,
        "historical_replay": True,
        "input_count": 1,
        "output_count": 1,
        "actual_network_calls": 0,
        "logical_evaluations": 1,
        "reused_checkpoint_count": 0,
        "stale_checkpoint_count": 0,
        "content_hash": "c" * 64,
        "real_orders": 0,
        "virtual_orders": 0,
        "scheduler": False,
    }
    comparison = [{
        "股票代码": "000001", "股票名称": "平安银行", "Quant排名": 1, "Quant分": 76.5,
        "V2初筛排名": 1, "V3初筛排名": 1, "排名变化": 0,
        "Event Opportunity Score": 1.0, "Evidence Confidence": 0.8,
        "Event Action": "KEEP", "Risk Action": "KEEP", "事件摘要": "事件",
        "主要来源": "example.com", "来源发布时间": NOW.isoformat(),
        "是否联网搜索降级": False, "最终是否进入V3 Top20": True,
    }]
    return export_run(
        root / "run-1",
        trade_date="2026-07-30",
        run_id="run-1",
        manifest=manifest,
        items=[item],
        snapshots=[snapshot],
        checkpoint_audit=[{"股票代码": "000001", "状态": "NEW"}],
        data_quality=[],
        comparison=comparison,
    )


def test_event_score_does_not_change_quant_score():
    stock = dict(STOCK)
    item = build_screening_item(stock, _review(), snapshot_id="s", checkpoint_status="NEW", weights=load_event_overlay_config()["weights"], risk_action_values=load_event_overlay_config()["risk_actions"])
    assert stock["total_score"] == item.quant_score == 76.5


def test_event_score_does_not_change_quant_rank():
    assert build_screening_item(STOCK, _review(), snapshot_id="s", checkpoint_status="NEW", weights=load_event_overlay_config()["weights"], risk_action_values=load_event_overlay_config()["risk_actions"]).quant_rank == STOCK["rank"]


def test_v2_and_v3_outputs_are_separate():
    assert "event_overlay" not in str(ROOT / "outputs" / "quant_v2_validation")


def test_existing_v2_prompt_unchanged():
    path = ROOT / "research" / "structured_validation.py"
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    assert before == hashlib.sha256(path.read_bytes()).hexdigest()
    assert (ROOT / "prompts" / "event_overlay_v3_flash.yaml").is_file()


def test_frozen_decision_unchanged():
    paths = list((ROOT / "outputs" / "quant_v2_validation" / "2026-07-24" / "frozen_decisions").rglob("*"))
    files = [path for path in paths if path.is_file()]
    assert files
    assert [(path, hashlib.sha256(path.read_bytes()).hexdigest()) for path in files] == [(path, hashlib.sha256(path.read_bytes()).hexdigest()) for path in files]


def test_direct_search_only_for_top100():
    assert load_event_overlay_config()["screening"]["input_top_n"] == 100


def test_direct_search_requires_explicit_enable():
    assert RunOptions(date.today(), datetime.now(timezone.utc)).enable_real_search is False


def test_v3_runner_loads_project_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, object] = {}

    def fake_load_dotenv(path: Path, *, override: bool) -> None:
        called.update(path=path, override=override)

    monkeypatch.setattr(v3_runner, "load_dotenv", fake_load_dotenv)
    v3_runner.load_runtime_environment()

    assert called == {"path": v3_runner.ROOT / ".env", "override": False}


def test_direct_search_provider_mode_is_separate_from_material_outcome():
    raw = _raw()
    raw.update({"status": DIRECT_SEARCH_FALLBACK, "provider_verified": False, "direct_search_used": True})
    review = _review(raw)
    assert review.search_status == "MATERIAL_EVENT_FOUND"
    assert review.direct_search_used
    assert review.confidence_discount_applied
    assert not review.production_eligible


def test_direct_search_empty_events_has_explicit_no_material_outcome():
    review = _review({
        "status": DIRECT_SEARCH_FALLBACK,
        "provider": "deepseek_v4_flash_direct_search",
        "provider_verified": False,
        "direct_search_used": True,
        "items": [],
    })
    assert review.search_status == "NO_MATERIAL_EVENT"
    assert review.direct_search_used is True


def test_direct_search_does_not_hide_evidence_conflict():
    raw = _raw("POSITIVE")
    raw.update({
        "status": DIRECT_SEARCH_FALLBACK,
        "provider_verified": False,
        "direct_search_used": True,
    })
    raw["items"].append(dict(
        raw["items"][0],
        event_direction="NEGATIVE",
        url="https://www.sse.com.cn/conflict",
        title="相反方向正式公告",
    ))
    review = _review(raw)
    assert review.search_status == "EVIDENCE_CONFLICT"
    assert review.conflicts == ["POSITIVE_AND_NEGATIVE_MATERIAL_EVENTS"]


def test_no_result_is_not_no_event():
    a = _review({"status": "NO_RESULT_FOUND", "provider": "mock", "items": []})
    b = _review({"status": "NO_MATERIAL_EVENT", "provider": "mock", "items": [], "search_completed": True})
    assert a.search_status == "NO_RESULT_FOUND" and b.search_status == "NO_MATERIAL_EVENT"


def test_search_failure_is_not_neutral():
    assert _review({"status": "SEARCH_FAILED", "provider": "mock", "items": []}).search_status == "SEARCH_FAILED"


def test_missing_url_cannot_trigger_hard_gate():
    assert _review(_raw("NEGATIVE", url=None, tier="tier_1", materiality=1)).risk_action != RiskAction.BLOCK


def test_future_source_is_rejected():
    assert not _review(_raw(published_at=NOW + timedelta(seconds=1))).material_events


def test_published_at_after_cutoff_is_rejected():
    assert "FUTURE_EVIDENCE_REJECTED" in _review(_raw(published_at=NOW + timedelta(days=1))).warnings


@pytest.mark.parametrize(
    ("source_type", "event_type", "age_hours", "expected_window"),
    [
        ("MARKET_NEWS", "INDUSTRY", 37, 36.0),
        ("媒体报道", "OTHER", 37, 36.0),
        ("OTHER", "ORDER", 73, 72.0),
        ("ANNOUNCEMENT", "ORDER", 169, 168.0),
        ("交易所公告", "ORDER", 169, 168.0),
    ],
)
def test_stale_evidence_is_retained_for_audit_but_not_scored(
    source_type: str,
    event_type: str,
    age_hours: int,
    expected_window: float,
):
    review = _review(_raw(
        "NEGATIVE",
        url=(
            "https://www.sse.com.cn/official-announcement"
            if expected_window == 168.0
            else "https://example.com/a"
        ),
        published_at=NOW - timedelta(hours=age_hours),
        source_type=source_type,
        event_type=event_type,
        materiality=1,
    ))
    item = review.material_events[0]
    assert item.temporal_status == "FRESHNESS_WINDOW_EXCEEDED"
    assert item.freshness_window_hours == expected_window
    assert item.score_eligible is False
    assert item.score_exclusion_reasons == ["FRESHNESS_WINDOW_EXCEEDED"]
    assert review.event_opportunity_score == 0
    assert review.evidence_confidence == 0
    assert review.breadth_score == 0
    assert review.risk_action == RiskAction.KEEP


def test_unknown_publish_time_is_audited_but_neutral():
    review = _review(_raw("NEGATIVE", published_at=None, materiality=1))
    assert len(review.material_events) == 1
    assert review.material_events[0].score_eligible is False
    assert review.material_events[0].point_in_time_safe is False
    assert review.event_opportunity_score == 0
    assert review.evidence_confidence == 0
    assert review.breadth_score == 0
    assert review.risk_action == RiskAction.KEEP


def test_naive_publish_time_is_unknown_and_cannot_score():
    raw = _raw("POSITIVE")
    raw["items"][0]["published_at"] = "2026-07-30T15:00:00"
    review = _review(raw)
    item = review.material_events[0]
    assert item.published_at is None
    assert item.temporal_status == "PUBLISH_TIME_UNKNOWN"
    assert item.score_eligible is False
    assert review.event_opportunity_score == 0


@pytest.mark.parametrize(
    "invalid",
    [float("nan"), float("inf"), float("-inf"), -0.1, 1.1, 90],
)
def test_non_finite_llm_scores_are_audited_but_never_scored(invalid):
    raw = _raw("POSITIVE")
    raw["items"][0].update(
        materiality=invalid,
        relevance=invalid,
        confidence=invalid,
    )
    review = _review(raw)
    item = review.material_events[0]
    assert item.materiality == 0.3
    assert item.relevance == 0.5
    assert item.confidence == 0.3
    assert item.score_eligible is False
    assert "INVALID_NUMERIC_EVIDENCE" in item.score_exclusion_reasons
    assert review.event_opportunity_score == 0
    assert "NO_SCORE_ELIGIBLE_EVIDENCE" in review.warnings


def test_provider_preserves_invalid_numeric_audit_marker():
    row = _raw()["items"][0]
    row["confidence"] = float("nan")
    sanitized = _sanitize_event_rows([row])[0]
    assert sanitized["confidence"] == 0.3
    assert sanitized["_invalid_numeric_fields"] == ["confidence"]


def test_missing_url_is_audited_but_not_scored():
    review = _review(_raw("POSITIVE", url=None, materiality=1))
    assert review.material_events[0].score_eligible is False
    assert "EVIDENCE_URL_INVALID_OR_MISSING" in review.material_events[0].score_exclusion_reasons
    assert review.event_opportunity_score == 0


def test_fresh_evidence_remains_score_eligible():
    review = _review(_raw(
        published_at=NOW - timedelta(hours=35),
        source_type="MARKET_NEWS",
        event_type="INDUSTRY",
    ))
    assert review.material_events[0].score_eligible is True
    assert review.event_opportunity_score > 0


def test_opportunity_uses_time_decay_within_window():
    fresh = _review(_raw(published_at=NOW - timedelta(hours=1), source_type="OTHER"))
    older = _review(_raw(published_at=NOW - timedelta(hours=60), source_type="OTHER"))
    assert 0 < older.event_opportunity_score < fresh.event_opportunity_score


def test_historical_replay_forbids_direct_search():
    service = object.__new__(EventOverlayShadowService)
    service.config = load_event_overlay_config()
    with pytest.raises(ValueError, match="HISTORICAL_REPLAY"):
        service._validate_options(RunOptions(date(2020, 1, 1), NOW, enable_real_search=True, stock_limit=5))


def test_full_real_search_requires_explicit_confirmation():
    service = object.__new__(EventOverlayShadowService)
    service.config = load_event_overlay_config()
    with pytest.raises(ValueError, match="CONFIRMED_FULL_TOP100"):
        service._validate_options(RunOptions(
            date.today(),
            datetime.now(timezone.utc),
            enable_real_search=True,
            stock_limit=100,
        ))


def test_historical_snapshot_can_be_reused(tmp_path):
    store = CheckpointStore(tmp_path / "c.json")
    contract = _contract()
    store.save(contract, _review().model_dump(mode="json"))
    assert store.audit(contract)["reuse_allowed"]


def test_no_historical_snapshot_returns_unavailable():
    raw = NoNetworkEventSearchProvider(historical=True).search(STOCK, decision_as_of_time=NOW, max_sources=8)
    assert raw["status"] == "HISTORICAL_EVIDENCE_UNAVAILABLE"


def test_tests_do_not_call_real_search():
    assert MockEventSearchProvider().search(STOCK, decision_as_of_time=NOW, max_sources=8)["network_calls"] == 0


def test_duplicate_reprints_form_one_event_cluster():
    raw = _raw()
    raw["items"] *= 2
    assert len(_review(raw).material_events) == 1


def test_canonical_url_tracking_variants_are_deduplicated():
    raw = _raw(url="https://example.com/a?utm_source=feed")
    raw["items"].append(dict(
        raw["items"][0],
        url="https://example.com/a?utm_medium=social",
        title="不同转载标题",
    ))
    review = _review(raw)
    assert len(review.material_events) == 1
    assert "DUPLICATE_EVIDENCE_COLLAPSED" in review.warnings


def test_semantic_reprints_keep_the_more_authoritative_source():
    raw = _raw(title="中国宝安：签署重大订单公告")
    raw["items"].append(dict(
        raw["items"][0],
        url="https://www.sse.com.cn/b",
        title="快讯：中国宝安签署重大订单",
        source_tier="tier_4",
    ))
    review = _review(raw)
    assert len(review.material_events) == 1
    assert review.material_events[0].domain == "www.sse.com.cn"
    assert review.material_events[0].source_tier == "tier_1"


def test_independent_sources_raise_confidence():
    one = _review(_raw()).evidence_confidence
    raw = _raw()
    second = dict(raw["items"][0], url="https://other.example.org/b", title="事件B")
    raw["items"].append(second)
    assert _review(raw).evidence_confidence > one


def test_same_original_source_not_counted_twice():
    raw = _raw()
    raw["items"].append(dict(raw["items"][0]))
    assert _review(raw).breadth_score == pytest.approx(1 / 3)


def test_revision_history_preserved():
    item = _review().material_events[0].model_copy(update={"revision": 2})
    assert item.revision == 2


def test_old_news_has_low_novelty():
    assert novelty_score(NOW - timedelta(days=30), NOW) < novelty_score(NOW - timedelta(hours=1), NOW)


def test_price_already_reacted_reduces_opportunity():
    assert reaction_adjusted_opportunity(3, 15) < 3


def test_exposure_estimate_and_confidence_are_separate():
    review = _review()
    assert review.event_opportunity_score != review.evidence_confidence


def test_confirmation_unavailable_is_null_not_zero():
    assert novelty_score(None, NOW) is None


def test_source_tier_is_config_driven():
    assert "tier_1" in load_event_overlay_config()["source_tiers"]


def test_source_tier_is_derived_from_url_not_llm_claim():
    claimed_official = _review(_raw(tier="tier_1"))
    claimed_unknown = _review(_raw(url="https://www.sse.com.cn/a", tier="tier_4"))
    assert claimed_official.material_events[0].source_tier == "tier_4"
    assert claimed_unknown.material_events[0].source_tier == "tier_1"
    assert "SOURCE_TIER_OVERRIDDEN_FROM_URL" in claimed_official.warnings


def test_source_tier_domain_match_rejects_suffix_spoof():
    review = _review(_raw(url="https://evil-sse.com.cn/a", tier="tier_1"))
    assert review.material_events[0].source_tier == "tier_4"


def test_normalizer_accepts_explicit_freshness_and_source_tier_policies():
    review = normalize_search_result(
        STOCK,
        _raw(source_type="OTHER"),
        decision_as_of_time=NOW,
        max_events=5,
        confidence_discount=0.7,
        freshness_policy={
            "market_news_hours": 1,
            "default_hours": 1,
            "announcement_hours": 1,
        },
        source_tier_policy={"tier_2": ["example.com"]},
    )
    assert review.material_events[0].source_tier == "tier_2"
    assert review.material_events[0].score_eligible is False


@pytest.mark.parametrize(
    ("source_type", "policy_key", "expected_window"),
    [
        ("监管披露", "announcement_source_types", 168.0),
        ("行情快报", "market_news_source_types", 36.0),
    ],
)
def test_configured_source_type_lists_select_freshness_window(
    source_type: str,
    policy_key: str,
    expected_window: float,
):
    policy = {
        "market_news_hours": 36,
        "default_hours": 72,
        "announcement_hours": 168,
        policy_key: [source_type],
    }
    review = normalize_search_result(
        STOCK,
        _raw(source_type=source_type),
        decision_as_of_time=NOW,
        max_events=5,
        confidence_discount=0.7,
        freshness_policy=policy,
        source_tier_policy={
            "tier_1" if policy_key == "announcement_source_types" else "tier_2": [
                "example.com"
            ]
        },
    )
    assert review.material_events[0].freshness_window_hours == expected_window


def test_media_url_cannot_self_claim_extended_announcement_window():
    raw = _raw(source_type="公司公告")
    raw["items"][0]["published_at"] = (NOW - timedelta(hours=80)).isoformat()
    review = normalize_search_result(
        STOCK,
        raw,
        decision_as_of_time=NOW,
        max_events=5,
        confidence_discount=0.7,
        freshness_policy={
            "market_news_hours": 36,
            "default_hours": 72,
            "announcement_hours": 168,
            "announcement_source_types": ["公司公告"],
        },
        source_tier_policy={"tier_3": ["example.com"]},
    )
    item = review.material_events[0]
    assert item.source_tier == "tier_3"
    assert item.freshness_window_hours == 72
    assert item.score_eligible is False
    assert "FRESHNESS_WINDOW_EXCEEDED" in item.score_exclusion_reasons


def test_official_source_not_forced_to_one():
    raw = _raw(tier="tier_1")
    raw["items"].append(dict(raw["items"][0], title="独立公告", url="https://sse.com.cn/b"))
    assert len(_review(raw).material_events) == 2


def test_event_score_range_minus3_to_plus3():
    assert -3 <= _review().event_opportunity_score <= 3


def test_positive_event_score():
    assert _review(_raw("POSITIVE")).event_opportunity_score > 0


def test_negative_event_score():
    assert _review(_raw("NEGATIVE")).event_opportunity_score < 0


def test_missing_component_does_not_default_to_zero():
    with pytest.raises(ValueError, match="EVENT_OVERLAY_SCORE_COMPONENT_MISSING"):
        build_screening_item(STOCK, _review(), snapshot_id="s", checkpoint_status="NEW", weights={}, risk_action_values={})


def test_screening_weights_sum_to_one():
    assert sum(load_event_overlay_config()["weights"].values()) == pytest.approx(1)


def test_block_overrides_high_screening_score():
    blocked = _review(_raw("NEGATIVE", url="https://www.sse.com.cn/a", tier="tier_4", materiality=1))
    safe = _review(_raw("POSITIVE"))
    config = load_event_overlay_config()
    rows = [
        build_screening_item({**STOCK, "stock_code": "000001", "rank": 1, "total_score": 99}, blocked, snapshot_id="a", checkpoint_status="NEW", weights=config["weights"], risk_action_values=config["risk_actions"]),
        build_screening_item({**STOCK, "stock_code": "000002", "rank": 2, "total_score": 40}, safe, snapshot_id="b", checkpoint_status="NEW", weights=config["weights"], risk_action_values=config["risk_actions"]),
    ]
    assert rank_items(rows, 1)[0].stock_code == "000002"


def test_watch_only_not_in_active_shadow():
    result = apply_shadow_deployment_limits([{"stock_code": "1", "risk_action": "WATCH_ONLY"}], market_regime="NEUTRAL")
    assert not result["active_shadow"]


def test_high_heat_low_breadth_high_crowding_demoted():
    assert reaction_adjusted_opportunity(3, 20) <= 0.6


def test_v3_top20_does_not_fill_with_blocked_stocks():
    config = load_event_overlay_config()
    row = build_screening_item(STOCK, _review(_raw("NEGATIVE", url="https://www.sse.com.cn/a", tier="tier_4", materiality=1)), snapshot_id="s", checkpoint_status="NEW", weights=config["weights"], risk_action_values=config["risk_actions"])
    assert not rank_items([row], 20)[0].selected_top20


def test_official_major_negative_can_block():
    assert _review(_raw("NEGATIVE", url="https://www.sse.com.cn/a", tier="tier_4", materiality=1)).risk_action == RiskAction.BLOCK


def test_two_independent_sources_require_manual_review():
    raw = _raw("POSITIVE")
    raw["items"].append(dict(raw["items"][0], title="负面", url="https://other.org/b", event_direction="NEGATIVE"))
    review = _review(raw)
    assert review.risk_action == RiskAction.WATCH_ONLY and review.requires_pro_review


def test_single_anonymous_source_cannot_hard_block():
    assert _review(_raw("NEGATIVE", tier="tier_4", materiality=1)).risk_action != RiskAction.BLOCK


def test_untraceable_llm_summary_cannot_block():
    assert _review(_raw("NEGATIVE", url=None, materiality=1)).risk_action != RiskAction.BLOCK


def test_same_risk_not_double_counted():
    raw = _raw("NEGATIVE")
    raw["items"] *= 2
    assert len(_review(raw).material_events) == 1


def test_decision_as_of_time_required():
    service = object.__new__(EventOverlayShadowService)
    service.config = load_event_overlay_config()
    with pytest.raises(ValueError):
        service._validate_options(RunOptions(date.today(), datetime.now()))


def test_checkpoint_exact_contract_reuses(tmp_path):
    store = CheckpointStore(tmp_path / "c.json")
    store.save(_contract(), _review().model_dump(mode="json"))
    assert store.audit(_contract())["reuse_allowed"]


def test_checkpoint_result_tampering_is_detected(tmp_path):
    path = tmp_path / "c.json"
    store = CheckpointStore(path)
    contract = _contract()
    store.save(contract, _review().model_dump(mode="json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["000001"]["result"]["event_opportunity_score"] = 3.0
    path.write_text(json.dumps(payload), encoding="utf-8")
    audit = CheckpointStore(path).audit(contract)
    assert audit["reuse_allowed"] is False
    assert "CHECKPOINT_RESULT_HASH_MISMATCH" in audit["reasons"]


def test_checkpoint_cross_stock_result_is_detected(tmp_path):
    path = tmp_path / "c.json"
    store = CheckpointStore(path)
    contract = _contract(stock_code="000002")
    store.save(contract, _review().model_dump(mode="json"))
    audit = store.audit(contract)
    assert audit["reuse_allowed"] is False
    assert "CHECKPOINT_RESULT_STOCK_CODE_MISMATCH" in audit["reasons"]


def test_checkpoint_two_store_instances_merge_without_lost_update(tmp_path):
    path = tmp_path / "c.json"
    first = CheckpointStore(path)
    second = CheckpointStore(path)
    first.save(_contract(), _review().model_dump(mode="json"))
    second_result = _review().model_dump(mode="json")
    second_result["stock_code"] = "000002"
    for event in second_result["material_events"]:
        event["stock_code"] = "000002"
    second.save(
        _contract(stock_code="000002"),
        second_result,
    )
    reloaded = CheckpointStore(path)
    assert set(reloaded.payload) == {"000001", "000002"}
    assert reloaded.audit(_contract())["reuse_allowed"]
    assert reloaded.audit(_contract(stock_code="000002"))["reuse_allowed"]


def test_event_overlay_file_lock_blocks_second_process(tmp_path):
    lock_path = tmp_path / "run.lock"
    with exclusive_file_lock(
        lock_path,
        timeout_seconds=1,
        stale_after_seconds=60,
    ):
        with pytest.raises(RuntimeError, match="EVENT_OVERLAY_LOCK_TIMEOUT"):
            with exclusive_file_lock(
                lock_path,
                timeout_seconds=0.05,
                stale_after_seconds=60,
            ):
                pass


def test_failed_search_checkpoint_is_never_reused(tmp_path):
    store = CheckpointStore(tmp_path / "c.json")
    store.save(_contract(), {"search_status": "SEARCH_FAILED"})
    assert store.payload["000001"]["execution_status"] == "FAILED"
    assert not store.audit(_contract())["reuse_allowed"]


def test_legacy_false_success_search_checkpoint_is_rejected(tmp_path):
    store = CheckpointStore(tmp_path / "c.json")
    store.save(_contract(), {"search_status": "SEARCH_FAILED"})
    store.payload["000001"]["execution_status"] = "SUCCESS"
    assert not store.audit(_contract())["reuse_allowed"]


def test_real_direct_search_marks_gateway_request_explicit(monkeypatch):
    captured = {}

    class Gateway:
        def chat(self, request):
            captured["request"] = request
            return LLMResponse(
                provider="deepseek",
                model="deepseek-v4-flash",
                content="{}",
                parsed_json={"items": []},
                structured_output={"items": []},
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
                latency_ms=1,
                request_hash="test",
                status="ok",
            )

    monkeypatch.setenv("LLM_REAL_CALLS_ENABLED", "true")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "configured-for-test")
    provider = DeepSeekFlashDirectSearchProvider(
        {"temperature": 0.1, "system": "test", "prompt_version": "test"},
        gateway=Gateway(),
    )
    result = provider.search(STOCK, decision_as_of_time=NOW, max_sources=3)
    assert result["network_calls"] == 1
    assert captured["request"].metadata["explicit_real_llm_test"] is True
    assert '"output_format": "json_object"' in captured["request"].messages[-1].content


def test_real_direct_search_accepts_prompt_events_field(monkeypatch):
    class Gateway:
        def chat(self, request):
            return LLMResponse(
                provider="deepseek",
                model="deepseek-v4-flash",
                content="{}",
                parsed_json={"events": [{"title": "周末公告"}]},
                structured_output={"events": [{"title": "周末公告"}]},
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
                latency_ms=1,
                request_hash="events-alias",
                status="ok",
            )

    monkeypatch.setenv("LLM_REAL_CALLS_ENABLED", "true")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "configured-for-test")
    provider = DeepSeekFlashDirectSearchProvider(
        {"temperature": 0.1, "system": "test", "prompt_version": "test"},
        gateway=Gateway(),
    )

    result = provider.search(STOCK, decision_as_of_time=NOW, max_sources=3)

    assert result["items"] == [{"title": "周末公告"}]


def test_real_direct_search_uses_anthropic_web_search_tool(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={
            "model": "deepseek-v4-flash",
            "stop_reason": "end_turn",
            "content": [{
                "type": "text",
                "text": json.dumps({"events": [{"title": "周末公告"}]}, ensure_ascii=False),
            }],
            "usage": {
                "input_tokens": 10,
                "output_tokens": 20,
                "server_tool_use": {"web_search_requests": 1},
            },
        })

    monkeypatch.setenv("LLM_REAL_CALLS_ENABLED", "true")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "configured-for-test")
    provider = DeepSeekFlashDirectSearchProvider(
        {"temperature": 0.1, "system": "test", "prompt_version": "test"},
        transport=httpx.MockTransport(handler),
        endpoint="https://example.test/anthropic/v1/messages",
    )

    result = provider.search(STOCK, decision_as_of_time=NOW, max_sources=3)

    assert result["items"][0]["title"] == "周末公告"
    assert "source_tier" not in result["items"][0]
    assert result["items"][0]["event_direction"] == "NEUTRAL"
    assert captured["payload"]["tools"][0]["type"] == "web_search_20250305"
    assert captured["payload"]["thinking"] == {"type": "disabled"}
    assert captured["payload"]["temperature"] == 0.1


def test_search_query_change_rejects_checkpoint(tmp_path):
    store = CheckpointStore(tmp_path / "c.json")
    store.save(_contract(), _review().model_dump(mode="json"))
    assert not store.audit(_contract(input_hash="9" * 64))["reuse_allowed"]


def test_search_timeout_checkpoint_is_not_reused(tmp_path):
    store = CheckpointStore(tmp_path / "timeout.json")
    store.save(_contract(), {"search_status": "SEARCH_TIMEOUT"})
    audit = store.audit(_contract())
    assert audit["reuse_allowed"] is False
    assert audit["reasons"] == ["CHECKPOINT_NOT_SUCCESSFUL"]


def test_event_evidence_change_rejects_checkpoint(tmp_path):
    store = CheckpointStore(tmp_path / "c.json")
    store.save(
        _contract(news_snapshot_hash="8" * 64),
        _review().model_dump(mode="json"),
    )
    assert not store.audit(_contract(news_snapshot_hash="7" * 64))["reuse_allowed"]


def test_prompt_hash_change_rejects_checkpoint(tmp_path):
    store = CheckpointStore(tmp_path / "c.json")
    store.save(_contract(), _review().model_dump(mode="json"))
    assert not store.audit(_contract(prompt_hash="9" * 64))["reuse_allowed"]


def test_market_regime_hash_change_rejects_checkpoint(tmp_path):
    store = CheckpointStore(tmp_path / "c.json")
    store.save(_contract(), _review().model_dump(mode="json"))
    assert not store.audit(_contract(market_regime_hash="9" * 64))["reuse_allowed"]


def test_export_only_creates_zero_business_calls():
    assert "EXPORT_ONLY" in (ROOT / "scripts" / "run_v3_event_overlay_shadow_once.py").read_text(encoding="utf-8")


def test_actual_market_regime_used():
    assert apply_shadow_deployment_limits([], market_regime="NEUTRAL") == {"active_shadow": [], "watch_pool": []}


def test_neutral_not_overwritten_by_risk_off():
    rows = [{"stock_code": "1", "risk_action": "KEEP", "binding_cluster": "A"}]
    assert len(apply_shadow_deployment_limits(rows, market_regime="NEUTRAL")["active_shadow"]) == 1


def test_theme_concentration_limit_preserved():
    rows = [{"stock_code": str(i), "risk_action": "KEEP", "binding_cluster": "A"} for i in range(2)]
    assert len(apply_shadow_deployment_limits(rows, market_regime="RISK_OFF")["active_shadow"]) == 1


def test_v3_remains_shadow():
    assert PRODUCTION_OR_SHADOW == "SHADOW"


def test_no_orders_created():
    assert load_event_overlay_config()["production_or_shadow"] == "SHADOW"


def test_scheduler_remains_disabled():
    assert RunOptions(date.today(), datetime.now(timezone.utc)).resume is False


def test_real_trading_remains_disabled():
    source = inspect.getsource(EventOverlayShadowService)
    assert "TradeOrder" not in source and "Virtual" not in source


def test_required_excel_sheets(workbook_artifact):
    assert validate_workbook(Path(workbook_artifact["workbook"]))["required_sheets"]


def test_stock_code_text_format(workbook_artifact):
    assert validate_workbook(Path(workbook_artifact["workbook"]))["stock_code_text_format"]


def test_evidence_source_visible(workbook_artifact):
    assert validate_workbook(Path(workbook_artifact["workbook"]))["evidence_source_visible"]


def test_search_status_visible(workbook_artifact):
    assert validate_workbook(Path(workbook_artifact["workbook"]))["search_status_visible"]


@pytest.mark.parametrize(
    "payload",
    ["=1+1", "+cmd", "-2+3", "@SUM(A1:A2)", '  =HYPERLINK("x")'],
)
def test_untrusted_spreadsheet_text_is_formula_safe(payload):
    assert _cell_value(payload).startswith("'")


def test_v2_v3_comparison_complete(workbook_artifact):
    workbook = load_workbook(workbook_artifact["workbook"], read_only=True)
    headers = [cell.value for cell in workbook["V2与V3对照"][1]]
    workbook.close()
    assert {"Quant排名", "V2初筛排名", "V3初筛排名", "Event Opportunity Score", "Risk Action"}.issubset(headers)


def test_run_manifest_contains_all_versions(workbook_artifact):
    manifest = json.loads((Path(workbook_artifact["output_dir"]) / "run_manifest.json").read_text(encoding="utf-8"))
    assert {manifest["decision_version"], manifest["screening_version"], manifest["event_review_version"], manifest["search_contract_version"]} == {DECISION_VERSION, SCREENING_VERSION, EVENT_REVIEW_VERSION, SEARCH_CONTRACT_VERSION}


def test_api_requires_version_or_run_id():
    with pytest.raises(HTTPException) as raised:
        _resolve_run(None, run_id=None, trade_date=None, screening_version=SCREENING_VERSION)
    assert raised.value.status_code == 422


def test_api_latest_run_skips_failed_shadow_runs():
    failed = SimpleNamespace(manifest_json={"final_status": "V3_REAL_SEARCH_CANARY_FAILED"})
    ready = SimpleNamespace(
        screening_version=SCREENING_VERSION,
        manifest_json={
            "final_status": "V3_EVENT_OVERLAY_SHADOW_READY",
            "database_publish_eligible": True,
        },
    )

    class Session:
        @staticmethod
        def scalars(_query):
            return [failed, ready]

    assert _resolve_run(
        Session(),
        run_id=None,
        trade_date=date(2026, 7, 31),
        screening_version=SCREENING_VERSION,
    ) is ready


def test_api_rejects_failed_shadow_run_even_when_it_is_only_row():
    failed = SimpleNamespace(
        screening_version=SCREENING_VERSION,
        manifest_json={"final_status": "V3_REAL_SEARCH_CANARY_FAILED"},
    )

    class Session:
        @staticmethod
        def scalars(_query):
            return [failed]

    with pytest.raises(HTTPException) as raised:
        _resolve_run(
            Session(),
            run_id="failed-run",
            trade_date=None,
            screening_version=SCREENING_VERSION,
        )
    assert raised.value.status_code == 404


def test_v2_v3_same_execution_contract():
    assert build_forward_ab_manifest(["1"], ["2"])["execution_contract"] == EXECUTION_CONTRACT


def test_event_score_rank_ic_sign():
    scores = [3, 2, 1]
    returns = [0.03, 0.02, -0.01]
    covariance = sum((a - 2) * (b - sum(returns) / 3) for a, b in zip(scores, returns))
    assert covariance > 0


def test_promote_demote_counterfactual():
    result = promote_demote_counterfactual(["1", "2"], ["2", "3"])
    assert result == {"promoted": ["3"], "demoted": ["1"], "kept": ["2"]}


def test_no_automatic_promotion():
    assert not build_forward_ab_manifest(["1"], ["2"])["automatic_promotion"]


def test_runner_publishes_only_complete_database_eligible_top100(
    tmp_path,
    monkeypatch,
):
    manifest = {
        "database_publish_eligible": True,
        "input_count": 100,
    }
    (tmp_path / "run_manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    calls = 0

    def publish():
        nonlocal calls
        calls += 1
        return {"status": "SUCCESS", "published": True}

    monkeypatch.setattr(v3_runner, "publish_internal_web_snapshot", publish)
    result = v3_runner._publish_full_v31_if_eligible({
        "status": "V3_EVENT_OVERLAY_SHADOW_READY",
        "artifacts": {"output_dir": str(tmp_path)},
    })

    assert result == {"status": "SUCCESS", "published": True}
    assert calls == 1


def test_runner_never_publishes_canary_or_failed_run(tmp_path, monkeypatch):
    (tmp_path / "run_manifest.json").write_text(
        json.dumps({
            "database_publish_eligible": False,
            "input_count": 5,
        }),
        encoding="utf-8",
    )

    def unexpected_publish():
        raise AssertionError("canary must not publish")

    monkeypatch.setattr(
        v3_runner,
        "publish_internal_web_snapshot",
        unexpected_publish,
    )
    canary = v3_runner._publish_full_v31_if_eligible({
        "status": "V3_EVENT_OVERLAY_SHADOW_READY",
        "artifacts": {"output_dir": str(tmp_path)},
    })
    failed = v3_runner._publish_full_v31_if_eligible({
        "status": "V3_REAL_SEARCH_CANARY_FAILED",
        "artifacts": {"output_dir": str(tmp_path)},
    })

    assert canary == {
        "status": "SKIPPED",
        "reason": "NOT_DATABASE_PUBLISH_ELIGIBLE",
    }
    assert failed == {"status": "SKIPPED", "reason": "RUN_NOT_READY"}


def test_formal_daily_runner_reuses_matching_full_without_business_call(monkeypatch):
    ready = {
        "run_id": "ready-full",
        "decision_as_of_time": "2026-08-02T22:56:00+08:00",
        "source_run_id": "source-run",
        "_output_dir": "ready-output",
    }
    monkeypatch.setenv("ENABLE_REAL_TRADING", "false")
    monkeypatch.setenv("LLM_REAL_CALLS_ENABLED", "true")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(v3_runner, "init_db", lambda: None)
    monkeypatch.setattr(
        v3_runner,
        "_expected_daily_contract",
        lambda *_args: {"source_run_id": "source-run"},
    )
    monkeypatch.setattr(
        v3_runner,
        "_find_matching_manifest",
        lambda *_args, input_count: ready if input_count == 100 else None,
    )
    monkeypatch.setattr(
        v3_runner,
        "_run_event_overlay_once",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must reuse")),
    )
    monkeypatch.setattr(
        v3_runner,
        "publish_internal_web_snapshot",
        lambda: {"status": "SUCCESS", "published": True},
    )

    report = v3_runner.run_daily_v31(date(2026, 7, 31))

    assert report["status"] == v3_runner.DAILY_REUSED
    assert report["actual_network_calls"] == 0
    assert report["checkpoint_reused"] == 100
    assert report["web_sync"]["published"] is True


def test_formal_daily_runner_reuses_canary_and_runs_only_full(monkeypatch):
    decision = "2026-08-02T22:56:00+08:00"
    canary = {
        "run_id": "ready-canary",
        "decision_as_of_time": decision,
        "source_run_id": "source-run",
        "_output_dir": "canary-output",
    }
    calls: list[RunOptions] = []
    monkeypatch.setenv("ENABLE_REAL_TRADING", "false")
    monkeypatch.setenv("LLM_REAL_CALLS_ENABLED", "true")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(v3_runner, "init_db", lambda: None)
    monkeypatch.setattr(
        v3_runner,
        "_expected_daily_contract",
        lambda *_args: {"source_run_id": "source-run"},
    )
    monkeypatch.setattr(
        v3_runner,
        "_find_matching_manifest",
        lambda *_args, input_count: canary if input_count == 5 else None,
    )

    def run_once(options):
        calls.append(options)
        return {
            "status": "V3_EVENT_OVERLAY_SHADOW_READY",
            "run_id": "full-run",
            "source_run_id": "source-run",
            "input_count": 100,
            "search_failure_count": 0,
            "provider_failure_count": 0,
        }

    monkeypatch.setattr(v3_runner, "_run_event_overlay_once", run_once)
    monkeypatch.setattr(
        v3_runner,
        "_publish_full_v31_if_eligible",
        lambda _report: {"status": "SUCCESS", "published": True},
    )

    report = v3_runner.run_daily_v31(date(2026, 7, 31))

    assert report["status"] == v3_runner.DAILY_READY
    assert report["canary"]["status"] == "REUSED_SUCCESSFUL_CANARY"
    assert len(calls) == 1
    assert calls[0].stock_limit == 100
    assert calls[0].confirm_full_search is True
    assert calls[0].decision_as_of_time.isoformat() == decision


def test_real_search_cannot_execute_after_target_preopen_cutoff(monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime(2026, 8, 3, 9, 25, tzinfo=timezone(timedelta(hours=8)))
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)

    monkeypatch.setattr(event_overlay_service, "datetime", FixedDateTime)
    service = object.__new__(EventOverlayShadowService)
    options = RunOptions(
        trade_date=date(2026, 7, 31),
        decision_as_of_time=datetime(
            2026, 8, 3, 9, 20, tzinfo=timezone(timedelta(hours=8))
        ),
        enable_real_search=True,
        stock_limit=5,
    )

    with pytest.raises(ValueError, match="REAL_SEARCH_EXECUTION_AFTER_PREOPEN_CUTOFF"):
        service._validate_preopen_contract(
            options,
            {"monday_trade_date": "2026-08-03"},
        )
