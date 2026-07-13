from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from backend.api import performance as performance_api
from backend.main import create_app
from database.base import Base
from database.session import create_engine_from_url
from review.performance_cache import PerformanceCacheManager
from review.performance_market import MarketDataBatchLoader
from review.selection_performance_service import SelectionPerformanceService


def test_performance_api_contract_uses_database_and_no_external_calls(tmp_path, monkeypatch) -> None:
    engine = create_engine_from_url(f"sqlite:///{(tmp_path / 'api.db').as_posix()}")
    import database.models  # noqa: F401
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def service_factory():
        session = factory()
        return session, SelectionPerformanceService(session, cache_manager=PerformanceCacheManager(tmp_path / "cache"), market_loader=MarketDataBatchLoader(session, tmp_path / "market"))

    def execute(run_id: str, job_id: str):
        session, service = service_factory()
        try:
            service.execute(run_id, job_id)
        finally:
            session.close()

    monkeypatch.setattr(performance_api, "_service", service_factory)
    monkeypatch.setattr(performance_api, "_execute", execute)
    client = TestClient(create_app())

    settings = client.get("/api/workbench/performance/settings")
    assert settings.status_code == 200
    assert settings.json()["data"]["default_lookback_value"] == 5
    methodology = client.get("/api/workbench/performance/methodology").json()["data"]
    assert methodology["no_llm_call_verified"] is True
    assert methodology["per_stock_api_call_count"] == 0

    started = client.post("/api/workbench/performance/run", json={"evaluation_end_date": "2026-07-10", "lookback_value": 5})
    assert started.status_code == 200
    run_id = started.json()["data"]["performance_run_id"]
    runs = client.get("/api/workbench/performance/runs").json()["data"]["items"]
    assert runs[0]["run_id"] == run_id
    assert runs[0]["status"] == "PARTIAL_SUCCESS"
    assert client.get("/api/workbench/performance/cohorts").json()["data"]["total"] == 0
    assert client.get("/api/workbench/performance/daily").json()["data"]["total"] == 0
    assert client.get("/api/workbench/performance/stocks").json()["data"]["total"] == 0
    invalidated = client.post("/api/workbench/performance/invalidate", json={"performance_run_id": run_id, "reason": "fixture correction"})
    assert invalidated.json()["data"]["status"] == "INVALIDATED"
