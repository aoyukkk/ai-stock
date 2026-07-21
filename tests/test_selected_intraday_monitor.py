from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml
from sqlalchemy import func, select

from database.models.intraday_monitor import IntradayMonitorAlert, IntradayMonitorPoolVersion, IntradayMonitorRule
from database.models.trading import TradeOrder
from database.session import get_engine, get_session, init_db
from intraday_monitor.broker import BrokerUnavailable, MarketDataRequestBroker, RequestPriority
from intraday_monitor.config import DEFAULTS, load_monitor_config
from intraday_monitor.coordinator import IntradayMonitorCoordinator, MiddayCompatibilityGuard, _RULE_STATE
from intraday_monitor.rules import IntradayAlertRuleEngine
from intraday_monitor.service import IntradayMonitorService, MonitorDomainError


SHANGHAI = ZoneInfo("Asia/Shanghai")


@pytest.fixture()
def monitor_db():
    engine = get_engine("sqlite:///:memory:")
    init_db(engine)
    session = get_session(engine)
    try:
        yield session
    finally:
        session.close()


def test_repository_config_is_disabled_shadow_and_advisory_only() -> None:
    root = Path(__file__).resolve().parents[1]
    raw = yaml.safe_load((root / "config/intraday_monitor.yaml").read_text(encoding="utf-8"))["intraday_monitor"]
    assert raw["enabled"] is False
    assert raw["integration_mode"] == "SHADOW"
    assert raw["observation_only"] is True
    assert raw["security"] == {"allow_order_creation": False, "allow_auto_action": False, "allow_llm_in_refresh_loop": False}


def test_pool_requires_confirmation_deduplicates_and_versions(monitor_db) -> None:
    service = IntradayMonitorService(monitor_db, load_monitor_config())
    row = service.create_session(date(2026, 7, 16))
    raw = [
        {"stock_code": "000001", "source": "DIRECT_SEARCH", "monitor_profile": "CANDIDATE_MONITOR", "priority": "NORMAL"},
        {"stock_code": "000001.SZ", "source": "MANUAL_SELECTION", "monitor_profile": "CANDIDATE_MONITOR", "priority": "HIGH"},
    ]
    preview = service.preview_pool(row.id, raw)
    assert preview["raw_count"] == 2
    assert preview["deduplicated_count"] == 1
    assert service.list_pool(row.id, page=1, page_size=50)["total"] == 0
    first = service.confirm_pool(row.id, raw, source="TEST", confirmed_by="TRADER")
    assert first["pool_version"] == 1
    second = service.confirm_pool(row.id, raw, source="TEST_REPEAT", confirmed_by="TRADER")
    assert second["pool_version"] == 2
    assert service.list_pool(row.id, page=1, page_size=50)["total"] == 1
    assert monitor_db.scalar(select(func.count()).select_from(IntradayMonitorPoolVersion)) == 2


def test_position_profile_never_guesses_position_fact(monitor_db) -> None:
    service = IntradayMonitorService(monitor_db, load_monitor_config())
    row = service.create_session(date(2026, 7, 16))
    with pytest.raises(MonitorDomainError, match="CONFIRMED_POSITION_REQUIRED"):
        service.preview_pool(row.id, [{"stock_code": "000001.SZ", "source": "DIRECT_SEARCH", "monitor_profile": "POSITION_RISK_MONITOR", "priority": "CRITICAL"}])


def test_broker_p0_reservation_preempts_normal_requests() -> None:
    broker = MarketDataRequestBroker()
    broker.reserve_midday()
    with pytest.raises(BrokerUnavailable, match="MIDDAY_RESOURCE_PREEMPTION"):
        with broker.request(RequestPriority.P3, "MONITOR"):
            pass
    broker.release_midday()
    with broker.request(RequestPriority.P3, "MONITOR") as lease:
        assert lease.priority == RequestPriority.P3


def test_midday_guard_preserves_pool_and_alert_rows(monitor_db) -> None:
    broker = MarketDataRequestBroker()
    service = IntradayMonitorService(monitor_db, load_monitor_config())
    row = service.create_session(date(2026, 7, 16))
    service.confirm_pool(row.id, [{"stock_code": "000001.SZ", "source": "DIRECT_SEARCH", "monitor_profile": "CANDIDATE_MONITOR", "priority": "NORMAL"}], source="TEST", confirmed_by="TRADER")
    service.transition(row.id, "start", market_session="MORNING")
    before_hash = row.pool_hash
    result = MiddayCompatibilityGuard(broker).begin_midday(row)
    monitor_db.commit()
    assert result["p0_granted"] is True
    assert row.status == "PAUSED_MIDDAY"
    assert row.pool_hash == before_hash
    assert service.list_pool(row.id, page=1, page_size=50)["total"] == 1


def test_rule_engine_blocks_price_rules_when_snapshot_is_stale() -> None:
    class Rule:
        rule_type = "ABOVE_MAX_ACCEPTABLE_PRICE"
        threshold_json = {"value": 10}
        comparison = ">"
        severity = "WARNING"

    engine = IntradayAlertRuleEngine()
    assert engine.evaluate(Rule(), {"latest": 11, "freshness_status": "STALE", "monitor_profile": "CANDIDATE_MONITOR"}) is None
    hit = engine.evaluate(Rule(), {"latest": 11, "freshness_status": "FRESH", "monitor_profile": "CANDIDATE_MONITOR", "stock_code": "000001.SZ"})
    assert hit is not None
    assert "不自动下单" in hit.message


def test_consecutive_hit_dedup_and_no_order_creation(monitor_db) -> None:
    _RULE_STATE.clear()
    config = load_monitor_config()
    service = IntradayMonitorService(monitor_db, config)
    session_row = service.create_session(date(2026, 7, 16))
    service.confirm_pool(session_row.id, [{
        "stock_code": "000001.SZ", "source": "MIDDAY_RECOMMENDATION",
        "monitor_profile": "CANDIDATE_MONITOR", "priority": "NORMAL", "max_acceptable_price": 10,
    }], source="TEST", confirmed_by="TRADER")
    service.transition(session_row.id, "start", market_session="MORNING")

    class Market:
        def refresh_stocks(self, codes, force=False):
            return {"items": [{"stock_code": codes[0], "latest": 11, "provider_time": "2026-07-16T10:00:00+08:00"}], "batch_count": 1, "cache_status": "MISS"}

    coordinator = IntradayMonitorCoordinator(monitor_db, config, market_service=Market(), broker=MarketDataRequestBroker())
    now = datetime(2026, 7, 16, 10, 0, 30, tzinfo=SHANGHAI)
    first = coordinator.refresh_once(session_row.id, now=now)
    second = coordinator.refresh_once(session_row.id, now=now)
    third = coordinator.refresh_once(session_row.id, now=now)
    assert first["alerts"] == []
    assert len(second["alerts"]) == 1
    assert third["alerts"] == []
    alert = monitor_db.scalar(select(IntradayMonitorAlert))
    assert alert.occurrence_count == 2
    assert monitor_db.scalar(select(func.count()).select_from(TradeOrder)) == 0


def test_frontend_uses_centered_tables_and_requires_explicit_start() -> None:
    root = Path(__file__).resolve().parents[1]
    view = (root / "frontend/src/views/RealtimeMonitorView.vue").read_text(encoding="utf-8")
    assert view.count("<CenteredDataTable") >= 7
    assert "<el-table" not in view
    assert "启动盯盘" in view
    assert "confirmMonitorPool" in view
    assert "window.setInterval" in view
    assert "LLM" in view and "不自动下单" in view


def test_source_pages_require_selection_and_confirmation_before_monitor_pool_merge() -> None:
    root = Path(__file__).resolve().parents[1]
    expected = {
        "ResultTableView.vue": ("FINAL_CANDIDATE", "ACTIVE_ORDER_PLAN"),
        "ManualSelectionView.vue": ("MANUAL_SELECTION",),
        "MiddayRecommendationView.vue": ("MIDDAY_RECOMMENDATION", "加入下午盯盘"),
        "PostCloseActionsView.vue": ("POST_CLOSE_ACTION", "HUMAN_POSITION", "AI_POSITION"),
    }
    for filename, markers in expected.items():
        view = (root / "frontend/src/views" / filename).read_text(encoding="utf-8")
        assert "<CenteredDataTable" in view
        assert "selectable" in view
        assert "useMonitorPool" in view
        for marker in markers:
            assert marker in view
    composable = (root / "frontend/src/composables/useMonitorPool.ts").read_text(encoding="utf-8")
    assert composable.count("ElMessageBox.confirm") == 2
    assert "不会自动启动监测" in composable


def test_monitor_api_contracts_exist_and_are_separate_from_midday() -> None:
    root = Path(__file__).resolve().parents[1]
    api = (root / "backend/api/intraday_monitor.py").read_text(encoding="utf-8")
    for path in ("/sessions", "/pool/preview", "/pool/confirm", "/midday-suggestions", "/rules", "/results", "/alerts", "/usage", "/events"):
        assert f'intraday-monitor{path}' in api
    assert "MiddayRecommendationService" not in api
    assert "TradeOrder(" not in api


def test_monitor_api_creates_only_draft_session_and_confirmed_pool(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient
    from backend.main import create_app
    from database.session import close_db

    close_db()
    monkeypatch.setenv("AI_TRADER_DB_PATH", str(tmp_path / "monitor-api.db"))
    try:
        client = TestClient(create_app())
        created = client.post("/api/workbench/intraday-monitor/sessions", json={"trade_date": "2026-07-16"}).json()["data"]
        assert created["status"] == "DRAFT"
        assert created["stock_count"] == 0
        body = {
            "monitor_session_id": created["id"],
            "source": "DIRECT_SEARCH",
            "confirmed_by": "LOCAL_TRADER",
            "items": [{"stock_code": "000001.SZ", "source": "DIRECT_SEARCH", "monitor_profile": "CANDIDATE_MONITOR", "priority": "NORMAL"}],
        }
        preview = client.post("/api/workbench/intraday-monitor/pool/preview", json=body).json()["data"]
        assert preview["deduplicated_count"] == 1
        empty = client.get("/api/workbench/intraday-monitor/pool", params={"monitor_session_id": created["id"]}).json()["data"]
        assert empty["total"] == 0
        confirmed = client.post("/api/workbench/intraday-monitor/pool/confirm", json=body).json()["data"]
        assert confirmed["confirmed"] is True
        current = client.get("/api/workbench/intraday-monitor/sessions/current", params={"trade_date": "2026-07-16"}).json()["data"]
        assert current["status"] == "READY"
        assert current["stock_count"] == 1
    finally:
        close_db()
