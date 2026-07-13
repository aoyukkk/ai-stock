from __future__ import annotations

import shutil
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from backend.workbench.historical import HistoricalPipelineRunResolver
from backend.workbench.service import WorkbenchService
from backend.api import workbench as workbench_api
from backend.main import create_app
from database.models.quant_run import QuantRankResult
from database.models.system import LLMUsage
from database.models.validation import (
    ModelValidationAllocation,
    ModelValidationOrderPlan,
    ModelValidationSample,
    ProCandidateReview,
)
from database.models.workbench import WorkbenchRunRegistry
from database.session import (
    DEFAULT_SQLITE_PATH,
    DatabaseError,
    assert_database_path_consistency,
    create_engine_from_url,
)


TRADE_DATE = date(2026, 7, 10)


@pytest.fixture()
def historical_session(tmp_path, monkeypatch):
    target = tmp_path / "historical.db"
    shutil.copy2(DEFAULT_SQLITE_PATH, target)
    url = f"sqlite:///{target.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    engine = create_engine_from_url(url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_latest_compatible_run_bundle_uses_one_chain_and_real_counts(historical_session) -> None:
    bundle = HistoricalPipelineRunResolver(historical_session).resolve(TRADE_DATE)

    assert bundle["source_mode"] == "DATABASE"
    assert bundle["pipeline_status"] == "PARTIAL_SUCCESS"
    assert bundle["quant_run_id"] == "quant-840384b9e637e1143f243083"
    assert bundle["manifest_id"] == "manifest-7788c77a840e4a2589b6348d1564d1bd"
    assert bundle["flash_run_id"] == "trader-demo-ca9eee27004b4fcda754"
    assert bundle["pro_run_id"] == "pro-resume-69a2fa9da496486d88f3"
    assert bundle["counts"] == {
        "quant": 5308, "flash": 105, "flash_success": 103, "flash_failure": 2,
        "llm_top": 20, "manual": 7, "both": 0, "manual_only": 7,
        "candidate": 27, "pro": 27, "order": 27, "position": 27,
        "fundamental": 27, "non_zero_position": 14,
    }
    assert bundle["consistency"] == {"status": "PASS", "differences": []}
    assert len(bundle["current_errors"]) == 2
    assert bundle["excel"]["status"] == "SUCCESS"
    assert bundle["excel"]["sha256_verified"] is True


def test_token_ledger_is_database_backed_deduplicated_and_not_zero(historical_session) -> None:
    token = HistoricalPipelineRunResolver(historical_session).resolve(TRADE_DATE)["token_usage"]
    assert token["source"] == "DATABASE_DEDUPED_LEDGER"
    assert token["used"] == 1_983_865
    assert token["limit"] == 5_000_000
    assert token["remaining"] == 3_016_135
    assert token["unavailable_usage_count"] > 0


def test_historical_reconciliation_is_idempotent_and_changes_no_business_rows(historical_session) -> None:
    business_models = (
        QuantRankResult, ModelValidationSample, ProCandidateReview,
        ModelValidationOrderPlan, ModelValidationAllocation, LLMUsage,
    )
    before = {model: historical_session.scalar(select(func.count()).select_from(model)) for model in business_models}
    service = WorkbenchService(historical_session)

    first = service.reconcile(TRADE_DATE)
    second = service.reconcile(TRADE_DATE)

    assert first["candidate_set_hash"] == second["candidate_set_hash"]
    assert historical_session.scalar(select(func.count()).select_from(WorkbenchRunRegistry).where(
        WorkbenchRunRegistry.trade_date == TRADE_DATE,
        WorkbenchRunRegistry.pipeline_run_id == first["pipeline_run_id"],
        WorkbenchRunRegistry.candidate_set_hash == first["candidate_set_hash"],
    )) == 1
    after = {model: historical_session.scalar(select(func.count()).select_from(model)) for model in business_models}
    assert after == before


def test_result_pages_read_the_resolved_run_ids(historical_session) -> None:
    service = WorkbenchService(historical_session)
    status = service.status(TRADE_DATE)
    pipeline_run_id = status["pipeline_run_id"]

    quant = service.list_quant(TRADE_DATE, page=1, page_size=200, pipeline_run_id=pipeline_run_id)
    flash = service.list_flash(TRADE_DATE, page=1, page_size=200, pipeline_run_id=pipeline_run_id)
    assert quant["total"] == 5308
    assert len(quant["items"]) == 200
    assert flash["total"] == 105
    assert len(flash["items"]) == 105
    assert len(service.manual_snapshot(TRADE_DATE, pipeline_run_id)) == 7
    assert len(service.final_results(TRADE_DATE, pipeline_run_id)) == 27
    assert len(service.order_position_results(TRADE_DATE, pipeline_run_id)) == 27
    assert len(service.fundamentals(TRADE_DATE, pipeline_run_id)) == 27


def test_historical_api_loads_existing_without_creating_pipeline_job(historical_session, monkeypatch) -> None:
    factory = sessionmaker(bind=historical_session.get_bind(), expire_on_commit=False)
    def get_service():
        session = factory()
        return session, WorkbenchService(session)

    monkeypatch.setattr(workbench_api, "_service", get_service)
    client = TestClient(create_app())
    dates = client.get("/api/workbench/available-dates").json()["data"]["items"]
    assert dates[0]["trade_date"] == "2026-07-10"
    loaded = client.post("/api/workbench/runs/load-existing", json={"trade_date": "2026-07-10"})
    assert loaded.status_code == 200
    assert loaded.json()["data"]["source_mode"] == "DATABASE"
    assert client.get("/api/workbench/quant/results", params={"trade_date": "2026-07-10", "page_size": 200}).json()["data"]["total"] == 5308
    assert client.get("/api/workbench/flash/results", params={"trade_date": "2026-07-10", "page_size": 200}).json()["data"]["total"] == 105

    flash_pages = [client.get("/api/workbench/flash/results", params={
        "trade_date": "2026-07-10", "page": page, "page_size": 50,
    }).json()["data"] for page in (1, 2, 3)]
    assert [len(item["items"]) for item in flash_pages] == [50, 50, 5]
    assert {item["total"] for item in flash_pages} == {105}
    assert {item["total_pages"] for item in flash_pages} == {3}
    assert {item["run_id"] for item in flash_pages} == {"trader-demo-ca9eee27004b4fcda754"}
    codes = [row["stock_code"] for item in flash_pages for row in item["items"]]
    assert len(codes) == len(set(codes)) == 105
    assert set(row["stock_code"] for row in flash_pages[0]["items"]).isdisjoint(
        row["stock_code"] for row in flash_pages[1]["items"]
    )
    assert client.get("/api/workbench/flash/results", params={"trade_date": "2026-07-10", "page": 0}).status_code == 422

    quant_first = client.get("/api/workbench/quant/results", params={"trade_date": "2026-07-10", "page": 1, "page_size": 50}).json()["data"]
    quant_second = client.get("/api/workbench/quant/results", params={"trade_date": "2026-07-10", "page": 2, "page_size": 50}).json()["data"]
    assert quant_first["total_pages"] == 107
    assert quant_first["items"][0]["stock_code"] != quant_second["items"][0]["stock_code"]


def test_same_day_has_multiple_runs_but_latest_compatible_chain_is_selected(historical_session) -> None:
    service = WorkbenchService(historical_session)
    runs = service.available_runs(TRADE_DATE)
    assert len(runs) >= 2
    assert runs[0]["pro_run_id"] == "pro-resume-69a2fa9da496486d88f3"
    assert runs[0]["candidate_set_hash"] == "7f99fc962f27fac8670a1b729df7b540436ca80d5ed64e5dc410556e22071422"
    assert len({(item["flash_run_id"], item["candidate_set_hash"]) for item in runs}) == len(runs)


def test_database_path_mismatch_is_blocked(tmp_path) -> None:
    first = f"sqlite:///{(tmp_path / 'one.db').as_posix()}"
    second = f"sqlite:///{(tmp_path / 'two.db').as_posix()}"
    with pytest.raises(DatabaseError, match="DATABASE_PATH_MISMATCH"):
        assert_database_path_consistency(first, second)


def test_frontend_uses_abort_guard_database_priority_and_centered_tables() -> None:
    store = (DEFAULT_SQLITE_PATH.parents[1] / "frontend/src/stores/workbench.ts").read_text(encoding="utf-8")
    api = (DEFAULT_SQLITE_PATH.parents[1] / "frontend/src/api/workbench.ts").read_text(encoding="utf-8")
    table = (DEFAULT_SQLITE_PATH.parents[1] / "frontend/src/components/common/CenteredDataTable.vue").read_text(encoding="utf-8")
    assert "AbortController" in store and "requestSequence" in store
    assert "availableDates" in store and "status.value = null" in store
    assert "/api/workbench/runs/load-existing" in api
    assert "/manual-selections/snapshot" in api
    assert 'align="center"' in table and 'header-align="center"' in table
    assert "vertical-align: middle" in table and "white-space: normal" in table
    assert 'emit("pagination-change", { page, pageSize: props.pageSize })' in table
    assert 'emit("pagination-change", { page: 1, pageSize })' in table
    assert 'layout="total, sizes, prev, pager, next, jumper"' in table
    assert "pointer-events: auto" in table


def test_paginated_views_keep_total_during_loading_and_share_one_contract() -> None:
    root = DEFAULT_SQLITE_PATH.parents[1]
    result_view = (root / "frontend/src/views/ResultTableView.vue").read_text(encoding="utf-8")
    performance_view = (root / "frontend/src/views/SelectionPerformanceView.vue").read_text(encoding="utf-8")
    run_history = (root / "frontend/src/views/RunHistoryView.vue").read_text(encoding="utf-8")
    assert '@pagination-change="handlePaginationChange"' in result_view
    assert "query.page = payload.page" in result_view
    assert "query.pageSize = payload.pageSize" in result_view
    load_body = result_view.split("async function load()", maxsplit=1)[1].split("function handlePaginationChange", maxsplit=1)[0]
    assert "total.value = 0" not in load_body
    assert "rows.value = []" not in load_body
    assert "current !== sequence" in result_view
    assert performance_view.count('@pagination-change=') == 3
    assert '@pagination-change="changePage"' in run_history
