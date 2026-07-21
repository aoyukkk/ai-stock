from __future__ import annotations

from datetime import date

from sqlalchemy import create_engine, func, select

from database.base import Base
from database.models.ifind_shadow import ExternalProviderUsage, MarketSnapshotShadow
from database.models.workbench import ManualSelectionRecord
from database.session import get_session, init_db
from datasource.ifind.http.models import IndexDailyBar, MinuteBar, RealtimeQuote
from datasource.ifind.shadow import IFindPersistencePurpose, IFindProviderRegistry, assert_persistence_purpose
from services.ifind_shadow_service import IFindShadowService, RealtimeMonitorPoolResolver
from services.ifind_tushare_comparison import IFindTushareComparisonService


class FakeProvider:
    def __init__(self) -> None:
        self.realtime_calls: list[list[str]] = []

    def get_realtime(self, codes: list[str]):
        self.realtime_calls.append(codes)
        return [RealtimeQuote(stock_code=code, datetime="2026-07-14T14:50:00+08:00", latest=10.0, open=9.5, high=10.2, low=9.4, volume=100, amount=1000, data_status="CLOSED_SESSION_FINAL") for code in codes]

    def get_index_latest_completed(self, codes: list[str], trade_date: date):
        return [IndexDailyBar(index_code=code, datetime=f"{trade_date.isoformat()}T15:00:00+08:00", open=1, high=2, low=0.5, close=1.5, volume=10, amount=20, data_status="CLOSED_SESSION_FINAL") for code in codes]

    def get_minute_bars(self, code: str, start: str, end: str, interval: str = "1m"):
        return [MinuteBar(stock_code=code, datetime="2026-07-14T14:59:00+08:00", open=10, high=11, low=9, close=10.5, volume=5, amount=52.5, data_status="CLOSED_SESSION_FINAL")]


def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    init_db(engine)
    return get_session(engine)


def test_shadow_realtime_batches_and_is_idempotent():
    db = session()
    provider = FakeProvider()
    service = IFindShadowService(db, provider=provider)
    codes = [f"60000{i}.SH" for i in range(6)]

    service.refresh_stocks(codes)
    service.refresh_stocks(codes)

    assert [len(batch) for batch in provider.realtime_calls] == [4, 2, 4, 2]
    assert db.scalar(select(func.count(MarketSnapshotShadow.id))) == 6
    assert db.scalar(select(func.count(ExternalProviderUsage.id))) == 4
    db.close()


def test_shadow_index_persistence_and_production_input_rejection():
    db = session()
    service = IFindShadowService(db, provider=FakeProvider())
    result = service.index_shadow(["000001.SH", "399001.SZ"], date(2026, 7, 14))
    assert result["comparison_status"] == "SHADOW_ONLY"
    assert result["would_change_market_direction"] is False
    assert service.list_indices(date(2026, 7, 14))[0]["provider"] == "IFIND_HTTP"
    try:
        assert_persistence_purpose(IFindPersistencePurpose.PRODUCTION_INPUT)
    except RuntimeError as exc:
        assert str(exc) == "IFIND_PRODUCTION_INPUT_NOT_APPROVED"
    else:
        raise AssertionError("production input must be rejected")
    db.close()


def test_monitor_pool_deduplicates_manual_codes():
    db = session()
    db.add(ManualSelectionRecord(trade_date=date(2026, 7, 14), stock_code="600000.SH", priority="HIGH", reason="manual"))
    db.commit()
    result = RealtimeMonitorPoolResolver(db).resolve(date(2026, 7, 14))
    assert result["count"] == 1
    assert result["items"][0]["origin"] == "MANUAL"
    assert len(result["pool_hash"]) == 64
    db.close()


def test_registry_defaults_to_shadow_and_disabled():
    entries = IFindProviderRegistry().entries()
    assert {entry.provider_name for entry in entries} == {"ifind_http_index", "ifind_http_realtime", "ifind_http_minute"}
    assert all(not entry.enabled and entry.integration_mode.value == "SHADOW" and entry.fallback_provider == "tushare" for entry in entries)


def test_dual_source_comparison_is_field_only_and_never_overwrites_official_source():
    result = IFindTushareComparisonService().compare(
        {"close": 10.00001, "volume": 100}, {"close": 10.0, "volume": 100}
    )
    assert result["status"] == "MATCH_WITH_TOLERANCE"
    assert result["official_source_unchanged"] is True
