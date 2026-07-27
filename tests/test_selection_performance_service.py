from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import select

from database.base import Base
from database.models.performance import SelectionCohort, SelectionCohortMember, SelectionPerformanceDaily
from database.models.quant_run import QuantRun
from database.models.validation import ModelValidationAllocation, ModelValidationOrderPlan, ModelValidationRun, ModelValidationSample, ProCandidateReview
from database.session import create_engine_from_url, get_session
from review.performance_cohorts import SelectionCohortResolver, V2_FACTOR_VERSION
from review.performance_cache import PerformanceCacheManager
from review.performance_market import MarketDataBatchLoader
from review.performance_schemas import MarketBar, PerformanceRequest
from review.selection_performance_service import SelectionPerformanceService


class FixtureMarketLoader(MarketDataBatchLoader):
    def __init__(self, session):
        super().__init__(session, Path("missing"))
        self.dates = [date(2026, 7, 3), date(2026, 7, 6), date(2026, 7, 7)]
        self.watermark = "fixture-watermark"

    def available_dates(self, end_date=None):
        return [day for day in self.dates if end_date is None or day <= end_date]

    def load(self, stock_codes, dates):
        values = {
            "000001.SZ": {
                date(2026, 7, 3): MarketBar("000001.SZ", date(2026, 7, 3), 10, 10, 10, 10, 9.5),
                date(2026, 7, 6): MarketBar("000001.SZ", date(2026, 7, 6), 10, 11, 10, 11, 10, 10),
                date(2026, 7, 7): MarketBar("000001.SZ", date(2026, 7, 7), 11, 12, 11, 12.1, 11, 10),
                date(2026, 7, 8): MarketBar("000001.SZ", date(2026, 7, 8), 12.1, 12.2, 11.8, 11.9, 12.1, -1.6529),
            },
            "000002.SZ": {
                date(2026, 7, 3): MarketBar("000002.SZ", date(2026, 7, 3), 20, 20, 20, 20, 20),
                date(2026, 7, 6): MarketBar("000002.SZ", date(2026, 7, 6), 20, 20, 18, 18, 20, -10),
                date(2026, 7, 7): MarketBar("000002.SZ", date(2026, 7, 7), 18, 18, 18, 18, 18, 0),
                date(2026, 7, 8): MarketBar("000002.SZ", date(2026, 7, 8), 18, 18.5, 18, 18.36, 18, 2),
            },
        }
        return {code: values[code] for code in stock_codes}, self.watermark


def _service(tmp_path: Path, pro_scores: tuple[float, float] = (70, 65)):
    engine = create_engine_from_url(f"sqlite:///{(tmp_path / 'performance.db').as_posix()}")
    import database.models  # noqa: F401
    Base.metadata.create_all(engine)
    session = get_session(engine)
    now = datetime(2026, 7, 3, 16, tzinfo=timezone.utc)
    run = ModelValidationRun(run_id="flash-1", quant_run_id="quant-1", run_data_manifest_id="manifest-1", run_mode="HISTORICAL_REPLAY", knowledge_mode="HISTORICAL", decision_time=now, base_market_trade_date=date(2026, 7, 3), target_trade_date=date(2026, 7, 6), real_llm=False, status="SUCCESS", request_hash="request-1")
    session.add(run)
    for rank, (code, source) in enumerate([("000001.SZ", "LLM"), ("000002.SZ", "MANUAL")], start=1):
        session.add(ModelValidationSample(validation_run_id="flash-1", quant_run_id="quant-1", run_data_manifest_id="manifest-1", rank=rank, stock_code=code, stock_name=f"样本{rank}", quant_scores={"total_score": 80-rank}, profile_version="v1", selected_at=now, screening_result={"llm_score": 70-rank, "screening_decision": "ADVANCE", "_trader_demo": {"selection_source": source, "trading_candidate": True}}))
        session.add(ModelValidationOrderPlan(validation_run_id="flash-1", quant_run_id="quant-1", run_data_manifest_id="manifest-1", stock_code=code, decision_time=now, base_market_trade_date=date(2026, 7, 3), target_trade_date=date(2026, 7, 6), status="DRAFT", temporal_status="PASS"))
        session.add(ModelValidationAllocation(validation_run_id="flash-1", allocation_run_id="allocation-1", account_snapshot_id="account-1", stock_code=code, relative_allocation_weight=0.5, suggested_position_percent=0.6 if rank == 1 else 0.4, suggested_capital_amount=100000, suggested_quantity=1000, estimated_max_loss=5000))
        session.add(ProCandidateReview(
            pro_resume_run_id="pro-1", flash_validation_run_id="flash-1", chunk_id="chunk-1",
            stock_code=code, pro_score=pro_scores[rank - 1], pro_rank=rank, priority="MEDIUM",
            final_summary="测试复核", key_strengths=[], key_risks=[], fundamental_quality="VERIFIED",
            quant_llm_consistency="CONSISTENT", manual_review_priority="MEDIUM", data_conflict=False,
            prompt_version="test", actual_model="mock", review_status="COMPLETED",
        ))
    session.commit()
    return session, SelectionPerformanceService(session, cache_manager=PerformanceCacheManager(tmp_path / "cache"), market_loader=FixtureMarketLoader(session))


def _add_complete_run(
    session,
    *,
    trade_date: date,
    run_id: str,
    quant_run_id: str,
    factor_version: str,
    status: str,
) -> None:
    now = datetime(2026, 7, 27, 8, tzinfo=timezone.utc)
    session.add(QuantRun(
        run_id=quant_run_id,
        request_hash=f"request-{quant_run_id}",
        run_mode="WEB_PUBLISHED_V2" if factor_version == V2_FACTOR_VERSION else "POST_MARKET_FINAL",
        decision_time=now,
        base_market_trade_date=trade_date,
        target_trade_date=date.fromordinal(trade_date.toordinal() + 1),
        factor_version=factor_version,
        config_snapshot={},
        data_manifest_id=f"manifest-{quant_run_id}",
        universe_count=1,
        filtered_count=1,
        scored_count=1,
        skipped_count=0,
        top_count=1,
        no_llm_call_verified=True,
        trade_date_cache_used=True,
        per_stock_api_call_count=0,
        temporal_status="PASS",
        actionable=factor_version == V2_FACTOR_VERSION,
        status="COMPLETED",
    ))
    session.add(ModelValidationRun(
        run_id=run_id,
        quant_run_id=quant_run_id,
        run_data_manifest_id=f"manifest-{quant_run_id}",
        run_mode="WEB_PUBLISHED_V2" if factor_version == V2_FACTOR_VERSION else "POST_MARKET_FINAL",
        knowledge_mode="STRUCTURED",
        decision_time=now,
        base_market_trade_date=trade_date,
        target_trade_date=date.fromordinal(trade_date.toordinal() + 1),
        real_llm=False,
        status=status,
        request_hash=f"request-{run_id}",
    ))
    session.add(ModelValidationSample(
        validation_run_id=run_id,
        quant_run_id=quant_run_id,
        run_data_manifest_id=f"manifest-{quant_run_id}",
        rank=1,
        stock_code="000001.SZ",
        stock_name="V2样本",
        quant_scores={"total_score": 80},
        profile_version="v2",
        selected_at=now,
        screening_result={
            "llm_score": 80,
            "_trader_demo": {"selection_source": "LLM", "trading_candidate": True},
        },
    ))
    session.add(ModelValidationOrderPlan(
        validation_run_id=run_id,
        quant_run_id=quant_run_id,
        run_data_manifest_id=f"manifest-{quant_run_id}",
        stock_code="000001.SZ",
        decision_time=now,
        base_market_trade_date=trade_date,
        target_trade_date=date.fromordinal(trade_date.toordinal() + 1),
        status="DRAFT",
        temporal_status="PASS",
    ))
    session.add(ModelValidationAllocation(
        validation_run_id=run_id,
        allocation_run_id=f"allocation-{run_id}",
        account_snapshot_id="account-1",
        stock_code="000001.SZ",
        relative_allocation_weight=1,
        suggested_position_percent=1,
        suggested_capital_amount=100000,
        suggested_quantity=1000,
        estimated_max_loss=5000,
    ))
    session.commit()


def test_service_creates_immutable_cohort_and_persists_returns(tmp_path: Path) -> None:
    session, service = _service(tmp_path)
    request = PerformanceRequest(evaluation_end_date=date(2026, 7, 7), lookback_value=5)
    started = service.start(request)
    detail = service.execute(started["performance_run_id"], started["job_id"])
    assert detail["status"] == "SUCCESS"
    assert detail["cohort_count"] == 1
    assert detail["stock_count"] == 2
    assert detail["daily_record_count"] == 4
    assert service.summary()["no_llm_call_verified"] is True
    assert service.summary()["per_stock_api_call_count"] == 0
    assert len(service.portfolio_daily()) == 2
    assert len(service.stock_daily()) == 4
    assert session.scalars(select(SelectionPerformanceDaily)).all()
    cohort = session.scalar(select(SelectionCohort))
    cohort.status = "CHANGED"
    try:
        session.commit()
        raise AssertionError("immutable cohort update was accepted")
    except ValueError as exc:
        assert "IMMUTABLE_SELECTION_SNAPSHOT" in str(exc)
        session.rollback()
    session.close()


def test_identical_success_cache_is_reused_and_snapshot_not_duplicated(tmp_path: Path) -> None:
    session, service = _service(tmp_path)
    request = PerformanceRequest(evaluation_end_date=date(2026, 7, 7))
    first = service.start(request)
    service.execute(first["performance_run_id"], first["job_id"])
    second = service.start(request)
    assert second["duplicate_status"] == "SUCCESS_CACHE_HIT"
    assert len(session.scalars(select(SelectionCohort)).all()) == 1
    assert len(session.scalars(select(SelectionCohortMember)).all()) == 2
    session.close()


def test_daily_monitor_uses_final_score_threshold_for_model_and_manual_sources(tmp_path: Path) -> None:
    session, service = _service(tmp_path, pro_scores=(60, 59.99))
    started = service.start(PerformanceRequest(evaluation_end_date=date(2026, 7, 7)))
    detail = service.execute(started["performance_run_id"], started["job_id"])
    members = session.scalars(select(SelectionCohortMember)).all()
    assert detail["stock_count"] == 1
    assert [(row.stock_code, row.selection_source, float(row.pro_score)) for row in members] == [
        ("000001.SZ", "LLM", 60.0),
        ("000002.SZ", "MANUAL", 59.99),
    ]
    cohort = session.scalar(select(SelectionCohort))
    assert cohort.config_snapshot_json["cohort_policy"]["contains_all_key_candidates"] is True
    selected = service.cohorts.members(cohort, "FINAL_CANDIDATES", True, True)
    key_candidates = service.cohorts.members(cohort, "KEY_CANDIDATES", True, True)
    assert [row.stock_code for row in selected] == ["000001.SZ"]
    assert [row.stock_code for row in key_candidates] == ["000001.SZ", "000002.SZ"]
    session.close()


def test_final_llm_only_requires_model_source_and_final_score_threshold(tmp_path: Path) -> None:
    session, service = _service(tmp_path, pro_scores=(60, 80))
    started = service.start(PerformanceRequest(evaluation_end_date=date(2026, 7, 7)))
    service.execute(started["performance_run_id"], started["job_id"])
    cohort = session.scalar(select(SelectionCohort))
    selected = service.cohorts.members(cohort, "FINAL_LLM_ONLY", True, True)
    assert [(row.stock_code, row.selection_source, float(row.pro_score)) for row in selected] == [
        ("000001.SZ", "LLM", 60.0),
    ]
    session.close()


def test_manual_candidate_at_threshold_is_included_in_today_recommendations(tmp_path: Path) -> None:
    session, service = _service(tmp_path, pro_scores=(59.99, 60))
    started = service.start(PerformanceRequest(evaluation_end_date=date(2026, 7, 7)))
    detail = service.execute(started["performance_run_id"], started["job_id"])
    assert detail["stock_count"] == 1
    cohort = session.scalar(select(SelectionCohort))
    selected = service.cohorts.members(cohort, "FINAL_CANDIDATES", True, True)
    assert [(row.stock_code, row.selection_source) for row in selected] == [("000002.SZ", "MANUAL")]
    session.close()


def test_daily_monitor_allows_empty_recommendation_cohort(tmp_path: Path) -> None:
    session, service = _service(tmp_path, pro_scores=(59.99, 10))
    started = service.start(PerformanceRequest(evaluation_end_date=date(2026, 7, 7)))
    detail = service.execute(started["performance_run_id"], started["job_id"])
    assert detail["status"] == "SUCCESS"
    assert detail["stock_count"] == 0
    assert len(session.scalars(select(SelectionCohortMember)).all()) == 2
    session.close()


def test_incremental_refresh_adds_only_new_date_and_is_idempotent(tmp_path: Path) -> None:
    session, service = _service(tmp_path)
    service.market.dates = [date(2026, 7, 3), date(2026, 7, 6)]
    first = service.start(PerformanceRequest(evaluation_end_date=date(2026, 7, 6)))
    service.execute(first["performance_run_id"], first["job_id"])
    assert len(service.stock_daily()) == 2
    service.market.dates.append(date(2026, 7, 7))
    pending = service.incremental_refresh(first["performance_run_id"], date(2026, 7, 7))
    result = service.execute_incremental(first["performance_run_id"], pending["job_id"], date(2026, 7, 7))
    assert result["added_evaluation_dates"] == 1
    assert result["added_stock_records"] == 2
    assert result["added_portfolio_records"] == 1
    assert result["duplicate_count"] == 0
    assert result["full_history_recalculated"] is False
    assert len(service.stock_daily()) == 4
    repeated = service.incremental_refresh(first["performance_run_id"], date(2026, 7, 7))
    assert repeated["status"] == "ALREADY_CURRENT"
    assert len(service.stock_daily()) == 4
    session.close()


def test_cache_tamper_is_reported_as_invalidated(tmp_path: Path) -> None:
    session, service = _service(tmp_path)
    started = service.start(PerformanceRequest(evaluation_end_date=date(2026, 7, 7)))
    service.execute(started["performance_run_id"], started["job_id"])
    path = service.cache.cache_dir / f"{started['performance_run_id']}.json"
    path.write_text(path.read_text(encoding="utf-8").replace("SUCCESS", "FAILED", 1), encoding="utf-8")
    status = service.cache_status(started["performance_run_id"])
    assert status["status"] == "INVALIDATED"
    assert status["reason"] == "CACHE_CHECKSUM_MISMATCH"
    session.close()


def test_historical_market_correction_invalidates_and_partial_is_not_reused(tmp_path: Path) -> None:
    session, service = _service(tmp_path)
    started = service.start(PerformanceRequest(evaluation_end_date=date(2026, 7, 7)))
    service.execute(started["performance_run_id"], started["job_id"])
    service.market.watermark = "corrected-market-watermark"
    status = service.cache_status(started["performance_run_id"])
    assert status["status"] == "INVALIDATED"
    assert status["reason"] == "HISTORICAL_MARKET_DATA_CHANGED"
    service.market.dates = [date(2026, 7, 3)]
    partial = service.start(PerformanceRequest(evaluation_end_date=date(2026, 7, 3), force_recalculate=True))
    service.execute(partial["performance_run_id"], partial["job_id"])
    assert service.run_detail(partial["performance_run_id"])["status"] == "PARTIAL_SUCCESS"
    repeated = service.start(PerformanceRequest(evaluation_end_date=date(2026, 7, 3)))
    assert repeated["duplicate_status"] == "CREATED"
    session.close()


def test_v2_cutover_prefers_completed_v2_run_over_newer_legacy_run(tmp_path: Path) -> None:
    session, _ = _service(tmp_path)
    trade_date = date(2026, 7, 24)
    _add_complete_run(
        session,
        trade_date=trade_date,
        run_id="v2-flash",
        quant_run_id="v2-quant",
        factor_version=V2_FACTOR_VERSION,
        status="COMPLETED",
    )
    _add_complete_run(
        session,
        trade_date=trade_date,
        run_id="legacy-flash",
        quant_run_id="legacy-quant",
        factor_version="TUSHARE_BASELINE_V1",
        status="SUCCESS",
    )

    cohorts = SelectionCohortResolver(session).resolve(trade_date, trade_date, 1)

    assert len(cohorts) == 1
    assert cohorts[0].flash_run_id == "v2-flash"
    assert cohorts[0].quant_run_id == "v2-quant"
    assert cohorts[0].config_snapshot_json["source_quant_factor_version"] == V2_FACTOR_VERSION
    session.close()


def test_v2_cutover_fails_closed_when_only_legacy_run_exists(tmp_path: Path) -> None:
    session, _ = _service(tmp_path)
    trade_date = date(2026, 7, 24)
    _add_complete_run(
        session,
        trade_date=trade_date,
        run_id="legacy-only-flash",
        quant_run_id="legacy-only-quant",
        factor_version="TUSHARE_BASELINE_V1",
        status="SUCCESS",
    )

    try:
        SelectionCohortResolver(session).resolve(trade_date, trade_date, 1)
        raise AssertionError("legacy run was accepted after the V2 cutover")
    except RuntimeError as exc:
        assert str(exc) == "V2_SELECTION_COHORT_REQUIRED:2026-07-24"
    session.close()
