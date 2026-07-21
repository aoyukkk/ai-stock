from __future__ import annotations

import hashlib
from datetime import datetime, time, timezone
from time import monotonic
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from database.models.ifind_shadow import MarketMinuteBarShadow, MarketSnapshotShadow
from database.models.intraday_monitor import IntradayMonitorAlert, IntradayMonitorItem, IntradayMonitorRefresh, IntradayMonitorRule, IntradayMonitorSession
from intraday_monitor.broker import BrokerUnavailable, RequestPriority, market_data_broker
from intraday_monitor.rules import IntradayAlertRuleEngine
from intraday_monitor.service import ACTIVE_ALERT_STATUSES
from services.ifind_shadow_service import IFindShadowService


SHANGHAI = ZoneInfo("Asia/Shanghai")
_RULE_STATE: dict[tuple[int, int], dict[str, Any]] = {}


class MiddayCompatibilityGuard:
    def __init__(self, broker=market_data_broker) -> None:
        self.broker = broker

    def begin_midday(self, session: IntradayMonitorSession | None) -> dict[str, Any]:
        self.broker.reserve_midday()
        if session and session.status in {"ACTIVE_MORNING", "PAUSING_FOR_MIDDAY"}:
            session.status = "PAUSED_MIDDAY"
            session.paused_at = datetime.now(timezone.utc)
            session.market_session = "MIDDAY"
        return {"p0_granted": True, "monitor_external_calls_during_midday": 0, "pool_preserved": True, "alerts_preserved": True}

    def end_midday(self) -> None:
        self.broker.release_midday()


class IntradayMonitorCoordinator:
    def __init__(self, session, config: dict[str, Any], *, market_service: Any | None = None, broker=market_data_broker) -> None:
        self.session = session
        self.config = config
        self.market_service = market_service or IFindShadowService(session)
        self.broker = broker
        self.engine = IntradayAlertRuleEngine()

    def tick(self, monitor_session_id: int, *, now: datetime | None = None) -> dict[str, Any]:
        now = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
        row = self.session.get(IntradayMonitorSession, monitor_session_id)
        if row is None: raise ValueError("MONITOR_SESSION_NOT_FOUND")
        market_session = _market_session(now)
        protection = self.config["midday_protection"]
        local_time = now.time().replace(tzinfo=None)
        if time.fromisoformat(protection["force_pause_at"]) <= local_time < time.fromisoformat(protection["afternoon_recheck_start_at"]):
            MiddayCompatibilityGuard(self.broker).begin_midday(row)
            self.session.commit()
            return {"status": row.status, "market_session": "MIDDAY", "external_calls": 0, "alerts": []}
        if market_session == "POST_MARKET":
            row.status, row.market_session, row.stopped_at = "POST_MARKET_STOPPED", market_session, datetime.now(timezone.utc)
            self.session.commit()
            return {"status": row.status, "market_session": market_session, "external_calls": 0, "alerts": []}
        if row.status not in {"ACTIVE_MORNING", "ACTIVE_AFTERNOON", "PAUSED_MIDDAY", "WAITING_AFTERNOON_RECHECK"}:
            return {"status": row.status, "market_session": market_session, "external_calls": 0, "alerts": []}
        if market_session == "AFTERNOON" and row.status in {"PAUSED_MIDDAY", "WAITING_AFTERNOON_RECHECK"}:
            row.status = "WAITING_AFTERNOON_RECHECK"
            self.session.commit()
            return self.afternoon_recheck(monitor_session_id, now=now)
        return self.refresh_once(monitor_session_id, now=now)

    def refresh_once(self, monitor_session_id: int, *, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(SHANGHAI)
        session_row = self.session.get(IntradayMonitorSession, monitor_session_id)
        items = list(self.session.scalars(select(IntradayMonitorItem).where(
            IntradayMonitorItem.monitor_session_id == monitor_session_id,
            IntradayMonitorItem.active.is_(True), IntradayMonitorItem.paused.is_(False),
        ).order_by(IntradayMonitorItem.priority, IntradayMonitorItem.stock_code)).all())
        codes = [item.stock_code for item in items]
        started = monotonic()
        refresh = IntradayMonitorRefresh(monitor_session_id=monitor_session_id, refresh_type="SNAPSHOT", priority="P3", started_at=datetime.now(timezone.utc), requested_codes=codes, returned_codes=[], missing_codes=[], external_calls=0, cache_hits=0, status="STARTED")
        self.session.add(refresh)
        self.session.commit()
        try:
            with self.broker.request(RequestPriority.P3, "INTRADAY_MONITOR", timeout_seconds=5):
                result = self.market_service.refresh_stocks(codes, force=False) if codes else {"items": [], "batch_count": 0, "cache_status": "EMPTY"}
        except BrokerUnavailable as exc:
            refresh.status, refresh.error_category = "PREEMPTED", str(exc)
            refresh.completed_at, refresh.latency_ms = datetime.now(timezone.utc), int((monotonic() - started) * 1000)
            self.session.commit()
            return {"status": "MIDDAY_RESOURCE_PREEMPTION", "external_calls": 0, "alerts": []}
        snapshots = {value["stock_code"]: value for value in result.get("items", [])}
        returned = sorted(snapshots)
        refresh.returned_codes, refresh.missing_codes = returned, sorted(set(codes) - set(returned))
        refresh.external_calls = int(result.get("batch_count", 0) if result.get("cache_status") != "CACHE_HIT" else 0)
        refresh.cache_hits = len(returned) if result.get("cache_status") == "CACHE_HIT" else 0
        refresh.status, refresh.completed_at = "SUCCESS", datetime.now(timezone.utc)
        refresh.latency_ms = int((monotonic() - started) * 1000)
        if session_row:
            session_row.external_call_count += refresh.external_calls
            session_row.cache_hit_count += refresh.cache_hits
        alerts = []
        for item in items:
            alerts.extend(self._evaluate_item(session_row, item, snapshots.get(item.stock_code), now))
        self.session.commit()
        return {"status": "SUCCESS", "requested": len(codes), "returned": len(returned), "missing": refresh.missing_codes, "external_calls": refresh.external_calls, "cache_hits": refresh.cache_hits, "latency_ms": refresh.latency_ms, "alerts": alerts, "broker": self.broker.snapshot()}

    def afternoon_recheck(self, monitor_session_id: int, *, now: datetime | None = None) -> dict[str, Any]:
        result = self.refresh_once(monitor_session_id, now=now)
        row = self.session.get(IntradayMonitorSession, monitor_session_id)
        if row and result["status"] == "SUCCESS":
            row.status, row.market_session, row.resumed_at = "ACTIVE_AFTERNOON", "AFTERNOON", datetime.now(timezone.utc)
            self.broker.release_midday()
            self.session.commit()
        result["recheck"] = "AFTERNOON_OPEN_RECHECK"
        return result

    def _evaluate_item(self, session_row: IntradayMonitorSession | None, item: IntradayMonitorItem, snapshot: dict[str, Any] | None, now: datetime) -> list[dict[str, Any]]:
        if not snapshot or snapshot.get("latest") is None: return []
        provider_dt = _provider_datetime(snapshot.get("provider_time"))
        age = abs((now.astimezone(timezone.utc) - provider_dt).total_seconds()) if provider_dt else 10**9
        freshness = "FRESH" if age <= 120 else "STALE"
        context = {**snapshot, "stock_code": item.stock_code, "stock_name": item.stock_name_snapshot, "monitor_profile": item.monitor_profile, "freshness_status": freshness, "age_seconds": age}
        rules = self.session.scalars(select(IntradayMonitorRule).where(IntradayMonitorRule.monitor_item_id == item.id, IntradayMonitorRule.enabled.is_(True))).all()
        created = []
        for rule in rules:
            hit = self.engine.evaluate(rule, context)
            state_key = (session_row.id, rule.id)
            state = _RULE_STATE.setdefault(state_key, {"hits": 0, "armed": True})
            if hit and state["armed"]:
                state["hits"] += 1
                if state["hits"] < rule.consecutive_hits_required:
                    continue
                alert = self._record_alert(session_row, item, rule, hit, snapshot, freshness)
                if alert:
                    state["armed"], state["hits"] = False, 0
                    created.append({"id": alert.id, "stock_code": alert.stock_code, "severity": alert.severity, "title": alert.title, "message": alert.message})
            elif hit:
                latest = self.session.scalars(select(IntradayMonitorAlert).where(
                    IntradayMonitorAlert.monitor_session_id == session_row.id,
                    IntradayMonitorAlert.rule_id == rule.id,
                ).order_by(IntradayMonitorAlert.triggered_at.desc()).limit(1)).first()
                if latest and latest.status in ACTIVE_ALERT_STATUSES:
                    latest.occurrence_count += 1
            elif not hit:
                state["hits"] = 0
                if _outside_hysteresis(rule, context):
                    state["armed"] = True
        return created

    def _record_alert(self, session_row: IntradayMonitorSession | None, item: IntradayMonitorItem, rule: IntradayMonitorRule, hit: Any, snapshot: dict[str, Any], freshness: str) -> IntradayMonitorAlert | None:
        dedup = f"{session_row.trade_date}:{session_row.id}:{item.stock_code}:{rule.rule_type}:{rule.rule_version}:{item.plan_id or ''}:{item.position_snapshot_id or ''}"
        latest = self.session.scalars(select(IntradayMonitorAlert).where(IntradayMonitorAlert.dedup_key == dedup).order_by(IntradayMonitorAlert.triggered_at.desc()).limit(1)).first()
        now = datetime.now(timezone.utc)
        if latest and latest.status in ACTIVE_ALERT_STATUSES:
            latest.occurrence_count += 1
            return None
        if latest:
            last = _aware(latest.triggered_at)
            cooldown = 60 if hit.severity == "CRITICAL" else rule.cooldown_seconds
            if (now - last).total_seconds() < cooldown: return None
        daily_count = self.session.scalar(select(func.count()).select_from(IntradayMonitorAlert).where(IntradayMonitorAlert.dedup_key == dedup)) or 0
        if daily_count >= int(self.config["alerts"]["max_same_rule_alerts_per_day"]): return None
        alert = IntradayMonitorAlert(monitor_session_id=session_row.id, monitor_item_id=item.id, rule_id=rule.id, stock_code=item.stock_code, triggered_at=now, severity=hit.severity, title=hit.title, message=hit.message, current_value_json={"value": hit.current_value}, threshold_json={"value": hit.threshold, "comparison": hit.comparison}, market_snapshot_id=snapshot.get("id"), provider_time=str(snapshot.get("provider_time") or ""), freshness_status=freshness, status="TRIGGERED", dedup_key=dedup, occurrence_count=1)
        self.session.add(alert)
        self.session.flush()
        session_row.alert_count += 1
        if hit.severity == "CRITICAL": session_row.critical_alert_count += 1
        return alert


def _market_session(now: datetime) -> str:
    value = now.astimezone(SHANGHAI).time().replace(tzinfo=None)
    if time(9, 30) <= value < time(11, 30): return "MORNING"
    if time(11, 30) <= value < time(13, 0, 30): return "MIDDAY"
    if time(13, 0, 30) <= value < time(15, 0): return "AFTERNOON"
    if value >= time(15, 0): return "POST_MARKET"
    return "PRE_MARKET"


def _provider_datetime(value: Any) -> datetime | None:
    if not value: return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=SHANGHAI).astimezone(timezone.utc)
    except ValueError:
        return None


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _outside_hysteresis(rule: IntradayMonitorRule, context: dict[str, Any]) -> bool:
    current = IntradayAlertRuleEngine._current_value(rule.rule_type, context)
    target = (rule.threshold_json or {}).get("value")
    if current is None or target in (None, 0):
        return True
    try:
        current_value, threshold = float(current), float(target)
    except (TypeError, ValueError):
        return True
    margin = abs(threshold) * float(rule.hysteresis_percent) / 100
    if rule.comparison in {">", ">="}:
        return current_value < threshold - margin
    if rule.comparison in {"<", "<="}:
        return current_value > threshold + margin
    return current_value != threshold
