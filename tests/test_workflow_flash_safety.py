from __future__ import annotations

from datetime import date, datetime, timezone

from database.base import Base
from database.models.validation import (
    ModelValidationLLMAudit,
    ModelValidationRun,
    ModelValidationSample,
)
from database.models.quant_run import QuantRun
from database.session import create_engine_from_url, get_session
from backend.application.workflow import (
    WorkflowApplicationService,
    _finalize_interrupted_flash_run,
    _flash_tokens_for_date,
    _flash_usable_for_final,
    _mark_job_success,
    _partition_manual_rows,
    _safe_options,
    _select_flash_for_final,
)
from backend.workbench.service import WorkbenchService
from database.models.workbench import ManualSelectionRecord, PipelineJob
from research.structured_validation import FUNDAMENTAL_PROMPT_VERSION, SCREENING_PROMPT_VERSION
from trader_demo.service import TraderDemoService


TRADE_DATE = date(2026, 7, 13)


def test_checkpoint_options_accept_daily_pipeline_run_identifiers():
    options = {
        "confirm_budget": True,
        "quant_run_id": "quant-current",
        "manual_hash": "manual-hash",
        "flash_run_id": "flash-current",
        "contract_version": "contract-v3",
        "recompute": True,
    }

    assert _safe_options(options) == dict(sorted(options.items()))


def test_manual_rows_outside_actionable_quant_can_be_forced_to_llm_review():
    rows = [
        ManualSelectionRecord(
            trade_date=TRADE_DATE,
            stock_code="600722.SH",
            reason="manual",
            priority="HIGH",
        ),
        ManualSelectionRecord(
            trade_date=TRADE_DATE,
            stock_code="600730.SH",
            reason="manual",
            priority="HIGH",
        ),
    ]

    included, excluded = _partition_manual_rows(
        rows, {"600722"}, include_review_only=True
    )

    assert [row.stock_code for row in included] == ["600722.SH", "600730.SH"]
    assert excluded == []


def test_manual_rows_outside_quant_remain_excluded_without_explicit_policy():
    rows = [
        ManualSelectionRecord(
            trade_date=TRADE_DATE,
            stock_code="600730.SH",
            reason="manual",
            priority="HIGH",
        )
    ]

    included, excluded = _partition_manual_rows(rows, {"600722"})

    assert included == []
    assert [row.stock_code for row in excluded] == ["600730.SH"]


def _session():
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, get_session(engine)


def _run(run_id: str, *, status: str = "RUNNING", snapshot: dict | None = None):
    return ModelValidationRun(
        run_id=run_id,
        quant_run_id="quant-test",
        run_data_manifest_id="manifest-test",
        run_mode="POST_MARKET_FINAL",
        knowledge_mode="STRUCTURED_INPUT_ONLY",
        decision_time=datetime(2026, 7, 13, 7, 30, tzinfo=timezone.utc),
        base_market_trade_date=TRADE_DATE,
        target_trade_date=date(2026, 7, 14),
        real_llm=True,
        status=status,
        request_hash=f"hash-{run_id}",
        config_snapshot=snapshot or {},
        expected_universe_audit={},
        warnings=[],
    )


def test_flash_token_usage_is_summed_from_persisted_audits():
    engine, session = _session()
    try:
        run = _run("flash-token")
        session.add(run)
        session.add_all([
            ModelValidationLLMAudit(
                validation_run_id=run.run_id, stock_code=f"00000{index}.SZ",
                task="fundamental_structured_inference" if index == 1 else "screening",
                knowledge_mode=run.knowledge_mode, model_alias="flash", actual_model="flash",
                prompt_version="v5", status="ok", schema_status="PASS", request_hash=f"r{index}",
                input_tokens=100 * index, output_tokens=10 * index, cost_usd=0,
                latency_ms=1, cache_status="MISS", diagnostics={},
            )
            for index in (1, 2)
        ])
        session.commit()
        assert _flash_tokens_for_date(session, TRADE_DATE) == 330
        assert WorkbenchService(session)._flash_budget(TRADE_DATE)["used"] == 330
    finally:
        session.close()
        engine.dispose()


def test_interrupted_flash_with_samples_becomes_reusable_but_not_final():
    engine, session = _session()
    try:
        run = _run("flash-interrupted")
        session.add(run)
        session.add(ModelValidationSample(
            validation_run_id=run.run_id, quant_run_id=run.quant_run_id,
            run_data_manifest_id=run.run_data_manifest_id, rank=1, stock_code="000001.SZ",
            stock_name="test", quant_scores={}, profile_version="v1",
            selected_at=datetime.now(timezone.utc), fundamental_result={}, screening_result={},
            field_provenance={}, missing_fields=[],
        ))
        session.commit()
        _finalize_interrupted_flash_run(session, run.quant_run_id, RuntimeError("boom"))
        session.refresh(run)
        assert run.status == "PARTIAL_SUCCESS"
        assert run.config_snapshot["reusable_source_only"] is True
        assert _flash_usable_for_final(run) is False
    finally:
        session.close()
        engine.dispose()


def test_only_non_degenerate_complete_flash_is_usable_for_final():
    good = _run("flash-good", status="PARTIAL_SUCCESS", snapshot={
        "flash_batch_quality": {"degenerate": False, "usable_for_final": True},
        "reusable_source_only": False,
    })
    bad = _run("flash-bad", status="PARTIAL_SUCCESS", snapshot={
        "flash_batch_quality": {"degenerate": True, "usable_for_final": False},
        "reusable_source_only": True,
    })
    assert _flash_usable_for_final(good) is True
    assert _flash_usable_for_final(bad) is False


def test_final_flash_selection_honors_explicit_run_id():
    engine, session = _session()
    try:
        session.add(QuantRun(
            run_id="quant-test", request_hash="quant-hash", run_mode="POST_MARKET_FINAL",
            decision_time=datetime(2026, 7, 13, 7, 30, tzinfo=timezone.utc),
            base_market_trade_date=TRADE_DATE, target_trade_date=date(2026, 7, 14),
            config_snapshot={}, data_manifest_id="manifest-test", temporal_status="PASS",
            actionable=True, status="COMPLETED",
        ))
        for run_id in ("flash-current", "flash-stale"):
            session.add(_run(run_id, status="PARTIAL_SUCCESS", snapshot={
                "flash_batch_quality": {"degenerate": False, "usable_for_final": True},
                "reusable_source_only": False,
            }))
        session.commit()

        selected = _select_flash_for_final(session, TRADE_DATE, "flash-stale")

        assert selected is not None
        assert selected.run_id == "flash-stale"
        assert _select_flash_for_final(session, TRADE_DATE, "flash-missing") is None
    finally:
        session.close()
        engine.dispose()


def test_degenerate_flash_source_cannot_reuse_screening_but_can_reuse_fundamental():
    engine, session = _session()
    try:
        run = _run("flash-degenerate", status="PARTIAL_SUCCESS", snapshot={
            "flash_batch_quality": {"degenerate": True, "usable_for_final": False},
            "reusable_source_only": True,
        })
        sample = ModelValidationSample(
            validation_run_id=run.run_id, quant_run_id=run.quant_run_id,
            run_data_manifest_id=run.run_data_manifest_id, rank=1, stock_code="000001.SZ",
            stock_name="test", quant_scores={}, profile_version="v1",
            selected_at=datetime.now(timezone.utc), fundamental_result={"status": "ok"},
            screening_result={"llm_score": 50}, field_provenance={}, missing_fields=[],
        )
        session.add_all([run, sample])
        for task in ("fundamental_structured_inference", "structured_light_screening"):
            prompt_version = (
                FUNDAMENTAL_PROMPT_VERSION
                if task == "fundamental_structured_inference"
                else SCREENING_PROMPT_VERSION
            )
            session.add(ModelValidationLLMAudit(
                validation_run_id=run.run_id, stock_code=sample.stock_code, task=task,
                knowledge_mode=run.knowledge_mode, model_alias="flash", actual_model="flash",
                prompt_version=prompt_version, status="ok", schema_status="PASS", request_hash=task,
                input_tokens=100, output_tokens=10, cost_usd=0, latency_ms=1,
                cache_status="MISS", diagnostics={},
            ))
        session.commit()
        service = TraderDemoService(session)

        assert service._screening_reuse_allowed(run.run_id) is False
        assert service._task_from_sample(sample, "structured_light_screening") is None
        # Fundamental enrichment remains reusable, avoiding duplicate expensive work.
        assert service._task_from_sample(sample, "fundamental_structured_inference") is not None
    finally:
        session.close()
        engine.dispose()


def test_successful_flash_job_keeps_detailed_progress_counts():
    job = PipelineJob(
        job_id="job-test", job_type="FLASH", trade_date=TRADE_DATE,
        status="RUNNING", stage="FLASH", progress_current=106, progress_total=106,
        success_count=105, failure_count=1, token_usage=1234, cost_usd=0,
        run_ids={}, checkpoint={},
    )
    _mark_job_success(job, {"run_ids": {"flash_run_id": "flash-good"}})
    assert job.status == "SUCCESS"
    assert (job.progress_current, job.progress_total) == (106, 106)
    assert (job.success_count, job.failure_count) == (105, 1)
    assert job.run_ids == {"flash_run_id": "flash-good"}


def test_flash_resume_preserves_budget_confirmation_and_forces_new_job():
    engine, session = _session()
    try:
        failed = PipelineJob(
            job_id="job-failed", job_type="FLASH", trade_date=TRADE_DATE,
            status="PARTIAL_SUCCESS", stage="FAILED", progress_current=40, progress_total=100,
            success_count=39, failure_count=1, token_usage=1234, cost_usd=0,
            run_ids={}, checkpoint={"options": {"confirm_budget": True, "force": False}},
        )
        session.add(failed)
        session.commit()

        resumed = WorkflowApplicationService(session).resume(failed.job_id)
        replacement = session.query(PipelineJob).filter(PipelineJob.job_id == resumed["job_id"]).one()

        assert resumed["duplicate_status"] == "NEW_JOB"
        assert replacement.status == "PENDING"
        assert replacement.checkpoint["options"] == {
            "confirm_budget": True,
            "force": True,
            "resume_from_job_id": failed.job_id,
        }
    finally:
        session.close()
        engine.dispose()
