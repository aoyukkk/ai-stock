from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from database.models.ranking_evaluation import (
    ModelEffectivenessDataIssue,
    ModelEffectivenessStageItem,
    ModelEffectivenessStageSnapshot,
    RankingEvaluationSnapshot,
    RankingEvaluationSnapshotItem,
)
from database.session import create_engine_from_url, get_session, init_db
from services.ranking_evaluation.model_stage_constants import (
    EVALUATION_VERSION,
    FLASH_QUANT_COHORT_MISMATCH,
    RETURN_BASIS,
)
from services.ranking_evaluation.model_stage_export_service import REQUIRED_WORKSHEETS
from services.ranking_evaluation.model_stage_metrics import (
    StageObservation,
    actionability_status,
    aggregate_daily_metrics,
    compare_v2_v3,
    flash_metrics,
    quant_metrics,
    reliability_metrics,
)
from services.ranking_evaluation.model_stage_service import (
    ModelStageEffectivenessService,
)
from services.ranking_evaluation.trading_calendar_service import (
    RankingTradingCalendarService,
)


ROOT = Path(__file__).resolve().parents[1]
DAY = date(2026, 7, 30)
OPEN_DATES = [DAY + timedelta(days=value) for value in range(15) if (DAY + timedelta(days=value)).weekday() < 5]


@pytest.fixture()
def stage_session():
    engine = create_engine_from_url("sqlite:///:memory:")
    init_db(engine)
    session = get_session(engine)
    cohort = RankingEvaluationSnapshot(
        evaluation_version="RANKING_FORWARD_EFFECTIVENESS_V1",
        snapshot_id="cohort-v2-test",
        snapshot_run_id="cohort-run-test",
        ranking_trade_date=DAY,
        generated_at=datetime.fromisoformat("2026-07-30T17:00:00+08:00"),
        source_quant_run_id="quant-v2-test",
        model_name="TUSHARE_QUANT_V2_CORRECTED_SHADOW",
        score_version="v2",
        factor_version="TUSHARE_QUANT_V2_CORRECTED_SHADOW",
        production_or_shadow="SHADOW",
        price_basis="OFFICIAL_CLOSE",
        return_basis="RAW_CLOSE",
        top_n=100,
        source_input_hash="q" * 64,
        snapshot_hash="s" * 64,
        evaluation_scope="QUANT_PRE_LLM_TOP100",
        snapshot_origin="FORWARD_CAPTURE",
        overall_data_status="NORMAL",
        raw_artifacts_json={},
    )
    session.add(cohort)
    session.flush()
    for rank in range(1, 101):
        session.add(
            RankingEvaluationSnapshotItem(
                snapshot_id=cohort.id,
                stock_code=f"{rank:06d}",
                ts_code=f"{rank:06d}",
                stock_name=f"股票{rank}",
                original_rank=rank,
                quant_score=101 - rank,
                baseline_trade_date=DAY,
                baseline_close=10,
                baseline_price_source="LOCAL_CACHE",
                baseline_source_hash="b" * 64,
                original_group=f"G{(rank - 1) // 20 + 1}",
                source_row_number=rank,
                row_data_status="NORMAL",
            )
        )
    session.commit()
    try:
        yield session, cohort
    finally:
        session.close()
        engine.dispose()


def _stage_rows(count: int = 100, selected: int = 20):
    return [
        {
            "stock_code": f"{rank:06d}",
            "stock_name": f"股票{rank}",
            "original_quant_rank": rank,
            "quant_score": 101 - rank,
            "flash_input_member": True,
            "flash_score": 101 - rank,
            "flash_rank": rank,
            "selected_flag": rank <= selected,
            "schema_status": "PASS",
            "task_status": "SUCCESS",
            "data_status": "NORMAL",
        }
        for rank in range(1, count + 1)
    ]


def _capture(stage_session, *, version="FLASH_V2_TEST", rows=None, run_id="screen-test"):
    session, cohort = stage_session
    service = ModelStageEffectivenessService(
        session,
        calendar=RankingTradingCalendarService(open_dates=OPEN_DATES),
    )
    result = service.capture_stage(
        cohort_snapshot=cohort,
        stage_type="FLASH_V2",
        screening_run_id=run_id,
        screening_version=version,
        rows=rows or _stage_rows(),
        prompt_version="prompt-v1",
        prompt_hash="p" * 64,
        output_schema_version="schema-v1",
        checkpoint_contract_version="checkpoint-v1",
        decision_as_of_time=datetime.fromisoformat("2026-07-30T17:00:00+08:00"),
        output_available_at=datetime.fromisoformat("2026-07-30T17:05:00+08:00"),
    )
    return service, result


def _observations(*, inverse=False, random_replacement=False):
    rows = []
    for rank in range(1, 101):
        future = (rank if inverse else 101 - rank) / 1000
        selected = rank <= 20
        if random_replacement:
            selected = 11 <= rank <= 30
        rows.append(
            StageObservation(
                stock_code=f"{rank:06d}",
                quant_rank=rank,
                quant_score=101 - rank,
                future_return=future,
                flash_score=101 - rank,
                flash_rank=rank,
                selected=selected,
                event_score=101 - rank,
            )
        )
    return rows


def test_quant_top100_cohort_is_immutable(stage_session):
    service, first = _capture(stage_session)
    _, same = _capture(stage_session)
    assert first["content_hash"] == same["content_hash"]
    changed = _stage_rows()
    changed[0]["flash_score"] = 1
    with pytest.raises(ValueError, match="STAGE_SNAPSHOT_IMMUTABLE_CONFLICT"):
        _capture(stage_session, rows=changed)


def test_flash_bound_to_same_quant_cohort(stage_session):
    _, result = _capture(stage_session)
    assert result["cohort_match"] is True


def test_cohort_mismatch_blocks_incremental_metrics(stage_session):
    service, result = _capture(stage_session, rows=_stage_rows(99))
    stage = stage_session[0].scalar(select(ModelEffectivenessStageSnapshot))
    metrics = service.calculate_daily_metrics(stage.id, horizon=1)
    assert result["cohort_match"] is False
    assert metrics[0].metric_payload_json["issue_code"] == FLASH_QUANT_COHORT_MISMATCH


def test_extra_candidates_excluded_from_standard_cohort(stage_session):
    rows = _stage_rows() + [{**_stage_rows(1)[0], "stock_code": "999999", "original_quant_rank": 999}]
    _capture(stage_session, rows=rows)
    session = stage_session[0]
    extra = session.scalar(select(ModelEffectivenessStageItem).where(ModelEffectivenessStageItem.stock_code == "999999"))
    assert extra.nonstandard_extra is True and extra.cohort_item_id is None


def test_v2_v3_versions_are_isolated(stage_session):
    _capture(stage_session, version="V2", run_id="run-v2")
    _capture(stage_session, version="V3", run_id="run-v3")
    rows = list(stage_session[0].scalars(select(ModelEffectivenessStageSnapshot)))
    assert {row.screening_version for row in rows} == {"V2", "V3"}


def test_historical_results_not_overwritten(stage_session):
    _capture(stage_session)
    row = stage_session[0].scalar(select(ModelEffectivenessStageSnapshot))
    with pytest.raises(ValueError, match="IMMUTABLE"):
        row.prompt_version = "changed"
        stage_session[0].commit()
    stage_session[0].rollback()


def test_quant_perfect_rank_ic_positive_one():
    assert quant_metrics(_observations())["rank_ic"] == pytest.approx(1)


def test_quant_inverse_rank_ic_negative_one():
    assert quant_metrics(_observations(inverse=True))["rank_ic"] == pytest.approx(-1)


def test_quant_top20_bottom20_spread():
    assert quant_metrics(_observations())["top20_bottom20_spread"] > 0


def test_quant_group_monotonicity():
    assert quant_metrics(_observations())["monotonicity_label"] == "4/4"


def test_flash_score_ic():
    assert flash_metrics(_observations())["flash_score_ic"] == pytest.approx(1)


def test_flash_rank_ic_when_full_ranking_exists():
    assert flash_metrics(_observations())["flash_rank_ic"] == pytest.approx(1)


def test_flash_rank_ic_null_without_full_ranking():
    rows = _observations()
    rows[0] = StageObservation(**{**rows[0].__dict__, "flash_rank": None})
    result = flash_metrics(rows)
    assert result["flash_rank_ic"] is None
    assert result["flash_rank_status"] == "FULL_RANKING_NOT_AVAILABLE"


def test_missing_flash_score_not_zero():
    rows = _observations()
    rows[0] = StageObservation(**{**rows[0].__dict__, "flash_score": None})
    assert flash_metrics(rows)["flash_score_valid_count"] == 99


def test_failed_flash_stock_preserved():
    rows = _observations()
    rows[0] = StageObservation(**{**rows[0].__dict__, "task_status": "FAILED", "flash_score": None})
    assert reliability_metrics(rows, input_count=100)["input_count"] == 100


def test_selected_unselected_spread():
    assert flash_metrics(_observations())["selection_spread"] > 0


def test_flash_incremental_lift_vs_quant_top20():
    assert flash_metrics(_observations())["incremental_lift"] == pytest.approx(0)


def test_promote_demote_spread():
    result = flash_metrics(_observations(random_replacement=True))
    assert result["promote_demote_spread"] < 0


def test_top20_overlap():
    assert flash_metrics(_observations())["top20_overlap_rate"] == 1


def test_selected_count_below20_not_backfilled():
    rows = [StageObservation(**{**row.__dict__, "selected": row.quant_rank <= 7}) for row in _observations()]
    assert flash_metrics(rows)["actual_selected_count"] == 7


def test_blocked_stock_not_used_as_replacement():
    rows = [StageObservation(**{**row.__dict__, "selected": False, "risk_action": "BLOCK"}) if row.quant_rank == 1 else row for row in _observations()]
    assert flash_metrics(rows)["actual_selected_count"] == 19


def test_realized_top20_capture():
    assert flash_metrics(_observations())["realized_top20_capture"] == 1


def test_v3_vs_v2_lift():
    assert compare_v2_v3(
        {"flash_top20_mean_return": 0.01},
        {"flash_top20_mean_return": 0.03},
        same_cohort=True,
        same_decision_time=True,
        same_return_basis=True,
    )["v3_vs_v2_lift"] == pytest.approx(0.02)


def test_v2_v3_requires_same_cohort():
    assert compare_v2_v3({}, {}, same_cohort=False, same_decision_time=True, same_return_basis=True)["status"] == "V2_V3_COMPARISON_CONTRACT_MISMATCH"


def test_event_score_ic():
    assert flash_metrics(_observations())["event_score_ic"] == pytest.approx(1)


def test_promote_demote_event_counterfactual():
    rows = [StageObservation(**{**row.__dict__, "risk_action": "BLOCK"}) if row.quant_rank == 100 else row for row in _observations()]
    assert flash_metrics(rows)["blocked_missed_gain"] is not None


def test_historical_event_search_not_called(monkeypatch):
    trap = lambda *_args, **_kwargs: pytest.fail("historical search called")
    monkeypatch.setattr("urllib.request.urlopen", trap)
    assert flash_metrics(_observations())["flash_score_ic"] == pytest.approx(1)


def test_output_before_next_open_is_actionable():
    assert actionability_status(datetime(2026, 7, 30, 17), datetime(2026, 7, 31, 9, 30)) == "ACTIONABLE_BEFORE_NEXT_OPEN"


def test_late_output_non_actionable():
    assert actionability_status(datetime(2026, 7, 31, 10), datetime(2026, 7, 31, 9, 30)) == "LATE_OUTPUT_NON_ACTIONABLE"


def test_late_output_excluded_from_actionable_metrics():
    rows = [StageObservation(**{**row.__dict__, "actionable": row.quant_rank > 1}) for row in _observations()]
    assert flash_metrics(rows, actionable_only=True)["flash_score_valid_count"] == 99


def test_output_time_missing_is_flagged():
    assert actionability_status(None, datetime(2026, 7, 31, 9, 30)) == "OUTPUT_TIME_MISSING"


def test_future_outcome_not_backfilled_early():
    assert actionability_status(None, None) == "OUTPUT_TIME_MISSING"


def test_daily_metrics_calculated_before_weekly_average():
    result = aggregate_daily_metrics([{"incremental_lift": 1}, {"incremental_lift": 3}], metric="incremental_lift")
    assert result["mean"] == 2


def test_weekly_average_equal_weights_days():
    result = aggregate_daily_metrics([{"x": 0.01}, {"x": 0.05}], metric="x")
    assert result["mean"] == pytest.approx(0.03)


def test_bootstrap_uses_day_as_sampling_unit():
    result = aggregate_daily_metrics([{"x": value} for value in range(10)], metric="x")
    assert result["bootstrap_unit"] == "RANKING_DAY"


def test_unmatured_horizon_is_null():
    result = aggregate_daily_metrics([{"x": None}], metric="x")
    assert result["mean"] is None


def test_version_and_return_basis_not_mixed():
    assert EVALUATION_VERSION == "MODEL_STAGE_FORWARD_EFFECTIVENESS_V2"
    assert RETURN_BASIS == "RAW_CLOSE_SIGNAL_RETURN"


def test_schema_failure_in_denominator():
    rows = _observations()
    rows[0] = StageObservation(**{**rows[0].__dict__, "schema_status": "FAILED"})
    result = reliability_metrics(rows, input_count=100)
    assert result["schema_error_count"] == 1 and result["input_count"] == 100


def test_search_failure_not_neutral():
    rows = _observations()
    rows[0] = StageObservation(**{**rows[0].__dict__, "search_status": "SEARCH_FAILED", "flash_score": None})
    result = reliability_metrics(rows, input_count=100)
    assert result["search_failed_count"] == 1 and result["scoring_coverage_ratio"] == 0.99


def test_checkpoint_reuse_metrics():
    assert reliability_metrics(_observations(), input_count=100, checkpoint_reuse_count=80)["checkpoint_reuse_count"] == 80


def test_no_llm_calls_during_evaluation(monkeypatch):
    monkeypatch.setenv("LLM_REAL_CALLS_ENABLED", "false")
    assert "llm_gateway" not in (ROOT / "services/ranking_evaluation/model_stage_metrics.py").read_text(encoding="utf-8")


def test_no_external_search_during_evaluation():
    text = (ROOT / "services/ranking_evaluation/model_stage_service.py").read_text(encoding="utf-8")
    assert "requests." not in text and "web.run" not in text


def test_no_quant_changes():
    assert _sha(ROOT / "research/flash_v4.py") == "99f4a4aa63b7b91d41b49d29650568c7494554210c6a129ace4b69ccd5b7a123"


def test_existing_flash_prompt_hash_unchanged():
    assert _sha(ROOT / "research/structured_validation.py") == "eed97efda0b6805df457865b9970bd430bf57ed6e4e15b824efd77a9bd7feb4c"


def test_existing_v3_prompt_hash_unchanged():
    assert _sha(ROOT / "prompts/event_overlay_v3_flash.yaml") == "8293299b489e8c4680dbefb17ed7a55ce0f31e5f07b31591bf34e0b9403dc690"


def test_frozen_decision_hash_unchanged():
    path = ROOT / "outputs/quant_v2_validation/2026-07-24/frozen_decisions/monday-v2-20260727-51ab2f95c9a194d507e3/decision_snapshot.json"
    assert _sha(path) == "db08db02f1b2d486e04b58c600c39fd70f786cac705a4a54a35a3ff0ae61543f"


def test_no_orders_created(stage_session):
    _capture(stage_session)
    assert stage_session[0].scalar(select(func.count(ModelEffectivenessStageSnapshot.id))) == 1


def test_scheduler_disabled():
    for name in ("run_model_stage_effectiveness_daily_once.cmd", "run_model_stage_effectiveness_weekly_once.cmd"):
        assert "scheduler" not in (ROOT / name).read_text(encoding="utf-8").lower()


def test_real_trading_disabled():
    assert reliability_metrics([], input_count=0)["new_business_call_count"] == 0


def test_required_excel_sheets():
    assert len(REQUIRED_WORKSHEETS) == 15
    assert {"累计总览", "D1明细", "D10明细", "版本与审计"} <= set(REQUIRED_WORKSHEETS)


def test_excel_stock_code_text():
    text = (ROOT / "scripts/build_model_stage_effectiveness_excel.mjs").read_text(encoding="utf-8")
    assert 'header === "stock_code"' in text and 'numberFormat = "@"' in text


def test_api_requires_version():
    text = (ROOT / "backend/api/model_effectiveness.py").read_text(encoding="utf-8")
    assert "MODEL_VERSION_REQUIRED" in text and "screening_version: str | None = Query(None)" in text


def test_frontend_read_only():
    source = (ROOT / "frontend/src/api/modelEffectiveness.ts").read_text(encoding="utf-8")
    view = (ROOT / "frontend/src/views/ModelEffectivenessView.vue").read_text(encoding="utf-8")
    assert "apiPost" not in source and "apiPut" not in source and "只读 Shadow" in view


def test_run_manifest_contains_all_versions():
    source = (ROOT / "services/ranking_evaluation/model_stage_export_service.py").read_text(encoding="utf-8")
    for field in ("evaluation_version", "quant_factor_version", "screening_version", "execution_contract_version", "versions"):
        assert field in source


def test_data_quality_issues_visible(stage_session):
    _capture(stage_session, rows=_stage_rows(99))
    issues = list(stage_session[0].scalars(select(ModelEffectivenessDataIssue)))
    assert any(row.issue_code == "FLASH_INPUT_STOCK_MISSING" for row in issues)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
