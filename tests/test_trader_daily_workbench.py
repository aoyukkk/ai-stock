from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from backend.api import workbench as workbench_api
from backend.main import create_app
from backend.workbench.service import WorkbenchService
from database.base import Base
from database.models.system import LLMUsage
from database.models.stock import StockMaster
from database.models.workbench import ManualSelectionRecord, PipelineJob
from database.session import create_engine_from_url


def _session_factory(tmp_path):
    engine = create_engine_from_url(f"sqlite:///{(tmp_path / 'workbench.db').as_posix()}")
    import database.models  # noqa: F401

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_workbench_service_persists_manual_selection_settings_and_jobs(tmp_path, monkeypatch) -> None:
    factory = _session_factory(tmp_path)
    monkeypatch.setenv("DATABASE_URL", str(factory.kw["bind"].url))
    session = factory()
    service = WorkbenchService(session)
    trade_date = date(2026, 7, 10)

    updated = service.update_settings({"quant_top_n": 80, "llm_analysis_n": 60, "llm_top_n": 15})
    assert updated["quant_top_n"] == 80
    assert updated["llm_analysis_n"] == 60

    first = service.add_manual(trade_date, "600519", "关注回调", "HIGH", None)
    second = service.add_manual(trade_date, "600519.SH", "更新原因", "MEDIUM", None)
    assert first["id"] == second["id"]
    assert session.scalar(select(func.count()).select_from(ManualSelectionRecord)) == 1

    job = service.start_job("QUANT", trade_date, mode="MOCK")
    assert job["status"] == "SUCCESS"
    assert job["stage"] == "MOCK_COMPLETED"
    assert session.scalar(select(func.count()).select_from(PipelineJob)) == 1
    assert session.scalar(select(func.count()).select_from(LLMUsage)) == 0
    session.add(StockMaster(code="600519.SH", name="贵州茅台"))
    session.commit()
    assert service._stock_names([SimpleNamespace(stock_code="600519.SH")]) == {"600519.SH": "贵州茅台"}
    session.close()


def test_workbench_api_isolated_readback_and_secret_redaction(tmp_path, monkeypatch) -> None:
    factory = _session_factory(tmp_path)
    monkeypatch.setenv("DATABASE_URL", str(factory.kw["bind"].url))

    def get_service():
        session = factory()
        return session, WorkbenchService(session)

    monkeypatch.setattr(workbench_api, "_service", get_service)
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    client = TestClient(create_app())

    status = client.get("/api/workbench/status", params={"trade_date": "2026-07-10"})
    assert status.status_code == 200
    assert status.json()["data"]["real_trading_enabled"] is False
    check = client.post("/api/workbench/data/check", json={"trade_date": "2026-07-10"})
    assert check.status_code == 200
    assert check.json()["data"]["provider_contacted"] is False

    created = client.post("/api/workbench/manual-selections", json={
        "trade_date": "2026-07-10", "stock_code": "000001", "reason": "人工观察", "priority": "HIGH"
    })
    assert created.status_code == 200
    assert created.json()["data"]["stock_code"] == "000001"
    selection_id = created.json()["data"]["id"]
    updated = client.put(f"/api/workbench/manual-selections/{selection_id}", json={"reason": "更新原因", "priority": "MEDIUM"})
    assert updated.status_code == 200
    batch = client.post("/api/workbench/manual-selections/batch", json={
        "trade_date": "2026-07-10", "stock_codes": ["600519", "000001"], "reason": "批量观察", "priority": "LOW"
    })
    assert batch.status_code == 200
    assert batch.json()["data"]["count"] == 2

    secret = client.post("/api/workbench/secrets/tushare", json={"value": "test-only-secret"})
    assert secret.status_code == 200
    assert "test-only-secret" not in secret.text
    assert client.get("/api/workbench/secrets/status").json()["data"]["tushare"]["configured"] is True
    assert client.post("/api/workbench/secrets/tushare/test").json()["data"]["status"] == "READY"
    assert client.delete("/api/workbench/secrets/tushare").status_code == 200
    blocked_update = client.post("/api/workbench/data/update", json={"trade_date": "2026-07-10", "mode": "FORCE_REFRESH"})
    assert blocked_update.json()["error"]["code"] == "DATA_PROVIDER_NOT_ENABLED"

    job = client.post("/api/workbench/quant/run", json={"trade_date": "2026-07-10", "mode": "MOCK"})
    assert job.status_code == 200
    assert job.json()["data"]["stage"] == "MOCK_COMPLETED"
    exported = client.post("/api/workbench/export/excel", json={"trade_date": "2026-07-10", "mode": "MOCK"})
    assert exported.status_code == 200
    assert exported.json()["data"]["job_type"] == "EXPORT"
    assert client.get("/api/workbench/jobs", params={"trade_date": "2026-07-10"}).json()["data"]["items"]
