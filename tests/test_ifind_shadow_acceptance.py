from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import create_engine, select

from backend.core.config import get_app_config
from database.models import IFindShadowAcceptanceRun, MarketMinuteBarShadow
from database.session import get_session, init_db
from datasource.ifind.http.models import IndexDailyBar, IndexRealtimeQuote, MinuteBar, RealtimeQuote
from services.ifind_shadow_acceptance_service import IFindShadowAcceptanceService


class FakeClient:
    call_count = 0


class FakeAuth:
    auth_calls = 0


class AcceptanceProvider:
    def clear_cache(self):
        return None

    def _count(self):
        FakeClient.call_count += 1

    def get_index_daily(self, codes, start, end):
        self._count()
        return [IndexDailyBar(index_code=code, datetime=f"{start.isoformat()}T15:00:00+08:00", open=1, high=2, low=.5, close=1.5, volume=10, amount=20, data_status="CLOSED_SESSION_FINAL") for code in codes]

    def get_index_realtime(self, codes):
        self._count()
        return [IndexRealtimeQuote(index_code=code, datetime="2026-07-14T15:00:00+08:00", latest=1.5, open=1, high=2, low=.5, volume=10, amount=20, data_status="CLOSED_SESSION_FINAL") for code in codes]

    def get_realtime(self, codes):
        self._count()
        return [RealtimeQuote(stock_code=code, datetime="2026-07-14T15:00:00+08:00", latest=10, open=9, high=11, low=8, volume=10, amount=100, data_status="CLOSED_SESSION_FINAL") for code in codes]

    def get_minute_bars(self, code, start, end, interval="1m"):
        self._count()
        return [MinuteBar(stock_code=code, datetime=f"2026-07-14T14:{30 + index:02d}:00+08:00", open=10, high=11, low=9, close=10, volume=1, amount=10, data_status="CLOSED_SESSION_FINAL") for index in range(30)]


def make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    init_db(engine)
    return get_session(engine)


def test_closed_acceptance_persists_only_shadow_and_keeps_business_immutable(monkeypatch, tmp_path):
    db = make_session()
    service = IFindShadowAcceptanceService(db, app_config=get_app_config(), now=datetime.fromisoformat("2026-07-14T22:00:00+08:00"))
    fake = AcceptanceProvider()
    monkeypatch.setattr(service, "gate", lambda budget: {"passed": True, "missing": [], "integration_mode": "SHADOW", "max_external_calls": budget})
    monkeypatch.setattr(service, "_provider", lambda budget: (fake, FakeAuth(), FakeClient()))
    monkeypatch.setattr(service, "_tushare_daily", lambda trade_date: {
        code: {"open": 9, "high": 11, "low": 8, "close": 10, "vol": 10, "amount": 100}
        for code in ("600000.SH", "000001.SZ", "300750.SZ", "688981.SH")
    })

    result = service.run(mode="closed-session", trade_date=date(2026, 7, 14), stock_limit=4, minute_stock_count=1, max_external_calls=20, output_dir=tmp_path)

    assert result["status"] == "CLOSED_SESSION_ACCEPTED"
    assert result["business_immutability"]["passed"] is True
    assert result["external_call_count"] > 0
    assert db.scalars(select(MarketMinuteBarShadow)).all()
    assert db.scalar(select(IFindShadowAcceptanceRun).where(IFindShadowAcceptanceRun.acceptance_run_id == result["acceptance_run_id"])) is not None
    db.close()


def test_open_acceptance_waits_outside_session_without_provider_call(tmp_path):
    db = make_session()
    service = IFindShadowAcceptanceService(db, app_config=get_app_config(), now=datetime.fromisoformat("2026-07-14T12:00:00+08:00"))
    result = service.run(mode="open-session", trade_date=date(2026, 7, 14), max_external_calls=40, output_dir=tmp_path)
    assert result["status"] == "BLOCKED" or result["status"] == "WAITING_FOR_OPEN_SESSION_ACCEPTANCE"
    assert result.get("external_call_count", 0) == 0
    db.close()


def test_market_session_distinguishes_midday_break():
    db = make_session()
    service = IFindShadowAcceptanceService(db, app_config=get_app_config(), now=datetime.fromisoformat("2026-07-14T12:00:00+08:00"))
    assert service.market_session(datetime.fromisoformat("2026-07-14T12:00:00+08:00")) == "MIDDAY_BREAK"
    db.close()
