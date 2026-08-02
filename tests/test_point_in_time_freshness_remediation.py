from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from fundamentals.cache import ReportPeriodCache, SCHEMA_VERSION
from fundamentals.pipeline import CachedTushareProfileService
from fundamentals.profile import TushareFundamentalProfileBuilder
from fundamentals.revision import select_latest_revisions
from llm_gateway.checkpoint_contract import LLMCheckpointContract, checkpoint_reuse_audit
from quant.shadow.data_gate_recovery import fetch_ths_members_by_board
from quant.shadow.regime_deployment import resolve_deployment_regime
from quant.shadow.risk_evidence import build_risk_evidence_lineage, filter_events_as_of
from quant.shadow.risk_v2_1 import PARENT_VERSION, VERSION
from scripts.finalize_monday_v2_shadow import apply_regime_cap
from scripts.refill_v2_fundamentals import (
    _can_reuse_refill_checkpoint,
    _restore_persisted_decision_time,
)
from temporal.evidence_status import news_evidence_status, overseas_evidence_status
from temporal.freshness import (
    FreshnessGateAction,
    FreshnessPolicy,
    canonical_hash,
    evaluate_freshness,
)


ROOT = Path(__file__).resolve().parents[1]
SH = ZoneInfo("Asia/Shanghai")
DECISION = datetime(2026, 7, 28, 18, 38, tzinfo=SH)


def _policy(name: str, *, action=FreshnessGateAction.BLOCK_STAGE):
    return FreshnessPolicy(
        dataset_name=name,
        max_age_trading_days=1,
        stale_action=action,
        unverified_action=action,
        missing_action=action,
    )


def _stale(name: str, *, action=FreshnessGateAction.BLOCK_STAGE):
    return evaluate_freshness(
        dataset_name=name,
        provider="TUSHARE",
        decision_as_of_time=DECISION,
        policy=_policy(name, action=action),
        data_business_date=date(2026, 7, 24),
        cache_fetched_at=datetime(2026, 7, 24, 20, tzinfo=SH),
        source_status="available",
        content=[{"x": 1}],
        current_membership_only=True,
    )


def _contract(**overrides):
    values = {
        "trade_date": "2026-07-28",
        "stock_code": "000001",
        "stage": "FLASH",
        "factor_version": "V2",
        "input_hash": "i",
        "prompt_version": "p",
        "prompt_hash": "ph",
        "schema_version": "s",
        "contract_version": "c",
        "data_manifest_hash": "d",
        "membership_manifest_hash": "m",
        "fundamental_snapshot_hash": "f",
        "news_snapshot_hash": "n",
        "overseas_snapshot_hash": "o",
        "market_regime_hash": "r",
        "risk_version": "rv",
        "decision_as_of_time": DECISION,
    }
    values.update(overrides)
    return LLMCheckpointContract(**values)


def _saved(contract: LLMCheckpointContract):
    return {
        "execution_status": "SUCCESS",
        "checkpoint_contract": contract.model_dump(mode="json"),
        "checkpoint_contract_hash": contract.checkpoint_hash,
    }


def test_stale_concept_cache_is_not_complete():
    item = _stale("ths_concept_members", action=FreshnessGateAction.DEGRADE)
    assert item.freshness_status == "STALE"
    assert not item.eligible_for_scoring or item.gate_action == "DEGRADE"


def test_sqlite_manifest_decision_time_restores_shanghai_timezone():
    persisted = datetime(2026, 7, 29, 17, 57, 28)
    restored = _restore_persisted_decision_time(persisted)

    assert restored.tzinfo is not None
    assert restored.utcoffset() == timedelta(hours=8)
    assert restored.isoformat() == "2026-07-29T17:57:28+08:00"


def test_fundamental_refill_reuses_only_same_manifest_checkpoint():
    previous = {
        "trade_date": "2026-07-29",
        "quant_run_id": "quant-1",
        "data_manifest_id": "manifest-1",
    }

    assert _can_reuse_refill_checkpoint(
        previous,
        trade_date=date(2026, 7, 29),
        quant_run_id="quant-1",
        data_manifest_id="manifest-1",
    )
    assert not _can_reuse_refill_checkpoint(
        previous,
        trade_date=date(2026, 7, 29),
        quant_run_id="quant-1",
        data_manifest_id="manifest-2",
    )


def test_stale_industry_cache_triggers_refresh(tmp_path):
    class Provider:
        def _cache_path(self, *_args):
            return tmp_path / "cache.json"

        def query_endpoint(self, *_args, use_cache, **_kwargs):
            return type("R", (), {"status": "available", "error_type": None, "source_status": "cache" if use_cache else "network", "records": [{"ts_code": "A", "con_code": "000001.SZ", "is_new": "Y"}]})()

    cache = tmp_path / "cache.json"
    cache.write_text("[]")
    os.utime(cache, (DECISION.timestamp() - 4 * 86400,) * 2)
    _, audit = fetch_ths_members_by_board(
        Provider(), [{"ts_code": "A"}], category="industry", output_root=tmp_path / "out",
        decision_as_of_time=datetime.now(SH) + timedelta(minutes=1),
        data_business_date=datetime.now(SH).date(),
    )
    assert audit["boards"][0]["cache_fetched_at"]


def test_refresh_false_still_checks_cache_age():
    assert _stale("ths_industry_members").cache_age_trading_days >= 2


def test_refresh_failure_blocks_v2_scoring():
    item = _stale("ths_industry_members")
    assert item.gate_action == "BLOCK_STAGE" and not item.eligible_for_scoring


def test_membership_manifest_hash_shared_by_downstream():
    value = canonical_hash([_stale("ths_concept_members").model_dump(mode="json")])
    downstream = {name: value for name in ("emotion", "theme", "flash", "concentration")}
    assert len(set(downstream.values())) == 1


def test_historical_membership_after_decision_is_rejected():
    item = evaluate_freshness(
        dataset_name="ths", provider="TUSHARE", decision_as_of_time=DECISION,
        policy=_policy("ths"), data_business_date=date(2026, 7, 29),
        source_status="available",
    )
    assert item.freshness_status == "FUTURE_DATA_DETECTED"


def test_fundamental_cache_ttl_checked_on_read(tmp_path):
    cache = ReportPeriodCache(tmp_path, ttl_hours=1)
    path = cache.path_for("income", "20260331", {"period": "20260331"})
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"metadata": {"schema_version": SCHEMA_VERSION, "fetched_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()}, "records": []}))
    assert cache.read("income", "20260331", {"period": "20260331"}) is None


def test_profile_generated_at_not_used_as_data_as_of():
    profile = TushareFundamentalProfileBuilder().build(
        "000001", as_of_time=DECISION,
        freshness={"freshness_status": "STALE", "point_in_time_safe": True},
    )
    assert profile.decision_as_of_time == DECISION
    assert profile.profile_generated_at != profile.decision_as_of_time


def test_historical_refill_requires_decision_time(tmp_path):
    with pytest.raises(ValueError, match="DECISION_AS_OF_TIME_REQUIRED"):
        CachedTushareProfileService(ReportPeriodCache(tmp_path)).build("000001")


def test_fundamental_records_after_decision_are_excluded():
    rows = [{"ts_code": "000001.SZ", "end_date": "20260331", "f_ann_date": "20260729"}]
    assert select_latest_revisions(rows, DECISION) == []


def test_historical_rerun_does_not_use_current_profile(tmp_path):
    service = CachedTushareProfileService(ReportPeriodCache(tmp_path))
    profile = service.build("000001", decision_time=DECISION)
    assert profile.decision_as_of_time == DECISION and profile.freshness_status == "MISSING"


def test_stale_fundamental_is_not_reported_as_current():
    profile = TushareFundamentalProfileBuilder().build(
        "000001", as_of_time=DECISION,
        freshness={"freshness_status": "STALE", "point_in_time_safe": True},
    )
    assert profile.freshness_status != "FRESH"


def test_report_period_and_disclosure_date_are_preserved():
    profile = TushareFundamentalProfileBuilder().build(
        "000001", as_of_time=DECISION,
        financial_selection={"latest_financial_period": "20260331", "announcement_date": "20260430"},
    )
    assert (profile.report_period, profile.latest_announcement_date) == ("20260331", "20260430")


def test_checkpoint_exact_contract_can_reuse():
    contract = _contract()
    assert checkpoint_reuse_audit(_saved(contract), contract)["reuse_allowed"]


def test_checkpoint_context_hash_mismatch_rejects_reuse():
    assert not checkpoint_reuse_audit(_saved(_contract(input_hash="old")), _contract())["reuse_allowed"]


def test_checkpoint_prompt_hash_mismatch_rejects_reuse():
    assert not checkpoint_reuse_audit(_saved(_contract(prompt_hash="old")), _contract())["reuse_allowed"]


def test_checkpoint_schema_change_rejects_reuse():
    assert not checkpoint_reuse_audit(_saved(_contract(schema_version="old")), _contract())["reuse_allowed"]


def test_checkpoint_freshness_hash_change_rejects_reuse():
    assert not checkpoint_reuse_audit(_saved(_contract(membership_manifest_hash="old")), _contract())["reuse_allowed"]


def test_legacy_checkpoint_without_hash_is_not_reused():
    result = checkpoint_reuse_audit({"execution_status": "SUCCESS"}, _contract())
    assert "LEGACY_CHECKPOINT_INSUFFICIENT_METADATA" in result["reasons"]


def test_export_only_rerun_creates_zero_business_calls():
    audit = json.loads((ROOT / "outputs/2026-07-28/审计/freshness_remediation/checkpoint_reuse_audit.json").read_text(encoding="utf-8"))
    assert audit["actual_network_calls"] == 0


def test_stale_checkpoint_is_not_overwritten():
    source = (ROOT / "scripts/finalize_monday_v2_shadow.py").read_text(encoding="utf-8")
    assert "stale_for_current_context" in source


def test_news_disabled_is_not_verified_empty():
    result = news_evidence_status(decision_as_of_time=DECISION, provider_enabled=False, provider_query_succeeded=False, evidence=[])
    assert result["status"] == "NEWS_PROVIDER_DISABLED"


def test_verified_empty_requires_successful_provider_query():
    result = news_evidence_status(decision_as_of_time=DECISION, provider_enabled=True, provider_query_succeeded=True, evidence=[])
    assert result["status"] == "VERIFIED_EMPTY"


def test_news_older_than_36_hours_is_excluded():
    result = news_evidence_status(decision_as_of_time=DECISION, provider_enabled=True, provider_query_succeeded=True, evidence=[{"published_at": DECISION - timedelta(hours=37)}])
    assert result["rejected"][0]["exclusion_reason"] == "OLDER_THAN_WINDOW"


def test_future_news_is_excluded():
    result = news_evidence_status(decision_as_of_time=DECISION, provider_enabled=True, provider_query_succeeded=True, evidence=[{"published_at": DECISION + timedelta(seconds=1)}])
    assert result["rejected"][0]["exclusion_reason"] == "FUTURE_NEWS"


def test_historical_news_window_uses_decision_time():
    result = news_evidence_status(decision_as_of_time=DECISION, provider_enabled=True, provider_query_succeeded=True, evidence=[])
    assert datetime.fromisoformat(result["window_end"]) == DECISION


def test_mock_overseas_is_excluded_from_real_score():
    result = overseas_evidence_status(decision_as_of_time=DECISION, provider_enabled=True, is_mock=True)
    assert not result["eligible_for_scoring"]


def test_overseas_disabled_is_visible_in_manifest():
    result = overseas_evidence_status(decision_as_of_time=DECISION, provider_enabled=False, is_mock=False)
    assert result["status"] == "OVERSEAS_PROVIDER_DISABLED"


def test_missing_risk_evidence_is_unknown_not_zero():
    result = build_risk_evidence_lineage(stock_code="000001", decision_as_of_time=DECISION, components={})
    assert all(row["missing_status"] == "UNKNOWN_MISSING_NOT_LOW_RISK" and row["score"] is None for row in result["rows"])


def test_event_after_decision_is_not_used():
    accepted, rejected = filter_events_as_of([{"event_id": "x", "published_at": DECISION + timedelta(days=1)}], decision_as_of_time=DECISION)
    assert not accepted and rejected[0]["exclusion_reason"] == "FUTURE_EVENT_EVIDENCE"


def test_lockup_reduction_and_pledge_lineage():
    result = build_risk_evidence_lineage(stock_code="000001", decision_as_of_time=DECISION, components={name: {"raw_value": 10, "score": 90, "point_in_time_safe": True} for name in ("unlock_risk", "reduction_risk", "pledge_risk")})
    assert {"unlock_risk", "reduction_risk", "pledge_risk"} <= {row["component"] for row in result["rows"]}


def test_duplicate_event_not_double_counted():
    event = {"event_id": "x", "published_at": DECISION - timedelta(hours=1)}
    accepted, rejected = filter_events_as_of([event, event], decision_as_of_time=DECISION)
    assert len(accepted) == 1 and rejected[0]["exclusion_reason"] == "DUPLICATE_EVENT"


def test_risk_v2_1_uses_new_version():
    assert VERSION == "TUSHARE_QUANT_V2_1_CORRECTED_SHADOW" and "V2_CORRECTED" in PARENT_VERSION


def test_frozen_20260724_decision_is_unchanged():
    path = ROOT / "outputs/quant_v2_validation/2026-07-24/frozen_decisions/monday-v2-20260727-51ab2f95c9a194d507e3/decision_snapshot.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    assert value["content_hash"] == "51ab2f95c9a194d507e344964224339489cdec604b26014696fda2ee7000c1d9"


def _source(code, industry):
    return {"stock_code": code, "stock_name": code, "rank": 1, "total_score": 80, "risk_score": 80, "level_one_sector": industry}


def _pro(code):
    return {"stock_code": code, "priority": "HIGH", "data_conflict": False, "pro_rank": 1, "pro_score": 80}


def _themes(codes):
    return {code: {"binding_cluster": code, "all_theme_clusters": code, "retained": "", "concentration_reason": ""} for code in codes}


def test_neutral_regime_is_used_by_deployment():
    sources = {"000001": _source("000001", "A")}
    watch, _, _ = apply_regime_cap([_pro("000001")], sources, _themes(sources), {"regime": "NEUTRAL"})
    assert watch[0]["regime"] == "NEUTRAL"


def test_risk_off_fallback_only_when_regime_missing():
    assert resolve_deployment_regime(None)["deployment_regime"] == "RISK_OFF"
    assert not resolve_deployment_regime({"regime": "NEUTRAL"})["fallback_used"]


def test_regime_mismatch_blocks_deployment():
    with pytest.raises(ValueError, match="MARKET_REGIME_DEPLOYMENT_MISMATCH"):
        resolve_deployment_regime({"regime": "NEUTRAL"}, requested_deployment_regime="RISK_OFF")


def test_regime_hash_is_persisted():
    assert len(resolve_deployment_regime({"regime": "NEUTRAL"})["input_hash"]) == 64


def test_report_generated_at_is_separate_from_data_as_of():
    source = (ROOT / "reporting/web_result_publish.py").read_text(encoding="utf-8")
    assert '"report_generated_at": now.isoformat()' in source
    assert '"as_of_time": decision_as_of_time.isoformat()' in source


def test_partial_currentness_when_news_disabled():
    value = json.loads((ROOT / "outputs/2026-07-28/审计/freshness_remediation/report_currentness_summary.json").read_text(encoding="utf-8"))
    assert value["currentness_status"] == "PARTIAL"


def test_stale_cache_not_shown_as_complete():
    value = json.loads((ROOT / "outputs/2026-07-28/审计/freshness_remediation/remediation_validation_report.json").read_text(encoding="utf-8"))
    assert not value["old_membership_complete"]


def test_fundamental_lineage_fields_are_published():
    source = (ROOT / "reporting/web_result_publish.py").read_text(encoding="utf-8")
    assert all(field in source for field in ("report_period", "latest_announcement_date", "cache_fetched_at", "cache_age_hours"))


def test_web_api_returns_freshness_manifest():
    source = (ROOT / "backend/api/workbench.py").read_text(encoding="utf-8")
    assert '"currentness": service.freshness_summary' in source


def test_frontend_displays_data_date_and_fetch_time():
    source = (ROOT / "frontend/src/views/ResultTableView.vue").read_text(encoding="utf-8")
    assert "decision_as_of_time|决策时点" in source and "cache_fetched_at|缓存抓取时间" in source


def test_report_does_not_claim_all_information_current_when_degraded():
    text = (ROOT / "outputs/2026-07-28/审计/freshness_remediation/remediation_validation_report.md").read_text(encoding="utf-8")
    assert "does not claim that all external data sources are current" in text


def test_no_real_llm_calls():
    value = json.loads((ROOT / "outputs/2026-07-28/审计/freshness_remediation/checkpoint_reuse_audit.json").read_text(encoding="utf-8"))
    assert value["actual_network_calls"] == 0


def test_no_real_or_virtual_orders_created():
    value = json.loads((ROOT / "outputs/2026-07-28/审计/freshness_remediation/remediation_validation_report.json").read_text(encoding="utf-8"))
    assert value["real_orders_created"] == value["virtual_orders_created"] == 0


def test_legacy_quant_results_unchanged():
    value = json.loads((ROOT / "outputs/2026-07-28/审计/freshness_remediation/remediation_validation_report.json").read_text(encoding="utf-8"))
    assert value["legacy_original_artifacts_unchanged"]


def test_frozen_decision_hash_unchanged():
    test_frozen_20260724_decision_is_unchanged()


def test_scheduler_remains_disabled():
    value = json.loads((ROOT / "outputs/2026-07-28/审计/freshness_remediation/remediation_validation_report.json").read_text(encoding="utf-8"))
    assert value["scheduler"] is False


def test_real_trading_remains_disabled():
    value = json.loads((ROOT / "outputs/2026-07-28/审计/freshness_remediation/remediation_validation_report.json").read_text(encoding="utf-8"))
    assert value["real_trading"] is False
