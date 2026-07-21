from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select

from database.models.intraday_monitor import (
    IntradayMonitorAlert,
    IntradayMonitorAlertAction,
    IntradayMonitorItem,
    IntradayMonitorPoolVersion,
    IntradayMonitorRule,
    IntradayMonitorSession,
)
from database.models.midday import MiddayRecommendationResult, MiddayRecommendationRun
from database.models.stock import StockMaster
from database.models.order_plan import OrderPlan
from database.models.trading import Position
from database.models.post_close import TraderPositionSnapshot
from stock_codes import normalize_ts_code


ACTIVE_ALERT_STATUSES = {"TRIGGERED", "ACKNOWLEDGED", "COOLDOWN", "MUTED_UNTIL"}
VALID_PROFILES = {"CANDIDATE_MONITOR", "POSITION_RISK_MONITOR", "ORDER_PLAN_MONITOR", "CUSTOM_MONITOR"}
VALID_PRIORITIES = {"CRITICAL", "HIGH", "NORMAL", "LOW"}


class MonitorDomainError(RuntimeError):
    pass


class IntradayMonitorService:
    def __init__(self, session, config: dict[str, Any]) -> None:
        self.session = session
        self.config = config

    def create_session(self, trade_date: date) -> IntradayMonitorSession:
        existing = self.current(trade_date)
        if existing and existing.status not in {"POST_MARKET_STOPPED", "FAILED", "INTERRUPTED"}:
            return existing
        row = IntradayMonitorSession(
            trade_date=trade_date, status="DRAFT", market_session="UNKNOWN",
            config_snapshot_json=self.config, source_run_ids_json={},
        )
        self.session.add(row)
        self.session.commit()
        return row

    def current(self, trade_date: date | None = None) -> IntradayMonitorSession | None:
        query = select(IntradayMonitorSession).order_by(IntradayMonitorSession.created_at.desc())
        if trade_date:
            query = query.where(IntradayMonitorSession.trade_date == trade_date)
        return self.session.scalars(query.limit(1)).first()

    def history(self, *, page: int, page_size: int) -> dict[str, Any]:
        query = select(IntradayMonitorSession).order_by(IntradayMonitorSession.created_at.desc())
        total = self.session.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = self.session.scalars(query.offset((page - 1) * page_size).limit(page_size)).all()
        return _page([session_dict(row) for row in rows], total, page, page_size)

    def transition(self, session_id: int, action: str, *, market_session: str, now: datetime | None = None) -> IntradayMonitorSession:
        row = self._session(session_id)
        now = now or datetime.now(timezone.utc)
        transitions = {
            "start": ({"DRAFT", "READY", "INTERRUPTED"}, "ACTIVE_MORNING" if market_session == "MORNING" else "ACTIVE_AFTERNOON"),
            "pause": ({"ACTIVE_MORNING", "ACTIVE_AFTERNOON", "STARTING"}, "PAUSED_USER"),
            "resume": ({"PAUSED_USER", "PAUSED_DATA_SOURCE", "INTERRUPTED"}, "ACTIVE_MORNING" if market_session == "MORNING" else "ACTIVE_AFTERNOON"),
            "stop": ({"DRAFT", "READY", "ACTIVE_MORNING", "ACTIVE_AFTERNOON", "PAUSED_USER", "PAUSED_MIDDAY", "WAITING_AFTERNOON_RECHECK", "FAILED"}, "POST_MARKET_STOPPED"),
        }
        allowed, target = transitions[action]
        if row.status not in allowed:
            raise MonitorDomainError(f"INVALID_SESSION_TRANSITION:{row.status}:{action}")
        if action == "start" and row.stock_count == 0:
            raise MonitorDomainError("MONITOR_POOL_REQUIRED")
        row.status = target
        row.market_session = market_session
        if action == "start": row.started_at = now
        if action == "pause": row.paused_at = now
        if action == "resume": row.resumed_at = now
        if action == "stop": row.stopped_at = now
        self.session.commit()
        return row

    def preview_pool(self, session_id: int, items: list[dict[str, Any]]) -> dict[str, Any]:
        session_row = self._session(session_id)
        normalized = self._normalize_items(items)
        current_codes = {row.stock_code for row in self._items(session_id)}
        final_codes = set(normalized)
        hard_limit = int(self.config["pool"]["hard_max_stocks"])
        default_limit = int(self.config["pool"]["default_max_stocks"])
        if len(final_codes) > hard_limit:
            raise MonitorDomainError("MONITOR_POOL_HARD_LIMIT_EXCEEDED")
        return {
            "monitor_session_id": session_row.id, "raw_count": len(items), "deduplicated_count": len(normalized),
            "added_count": len(final_codes - current_codes), "removed_count": len(current_codes - final_codes),
            "warning": "当前盯盘池已达到20只。请移除股票或调整优先级。" if len(final_codes) > default_limit else None,
            "pool_hash": _pool_hash(normalized), "items": list(normalized.values()),
        }

    def confirm_pool(self, session_id: int, items: list[dict[str, Any]], *, source: str, confirmed_by: str, source_midday_run_id: str | None = None) -> dict[str, Any]:
        preview = self.preview_pool(session_id, items)
        row = self._session(session_id)
        version = row.pool_version + 1
        pool_version = IntradayMonitorPoolVersion(
            monitor_session_id=session_id, version=version, source=source,
            source_midday_run_id=source_midday_run_id, raw_count=preview["raw_count"],
            deduplicated_count=preview["deduplicated_count"], added_count=preview["added_count"],
            removed_count=preview["removed_count"], pool_hash=preview["pool_hash"], confirmed_by=confirmed_by,
        )
        self.session.add(pool_version)
        self.session.flush()
        incoming = {item["stock_code"]: item for item in preview["items"]}
        existing = {item.stock_code: item for item in self._items(session_id, include_inactive=True)}
        for code, item in existing.items():
            if code not in incoming:
                item.active = False
        for code, payload in incoming.items():
            item = existing.get(code)
            if item is None:
                item = IntradayMonitorItem(monitor_session_id=session_id, stock_code=code, pool_version_id=pool_version.id)
                self.session.add(item)
            item.pool_version_id = pool_version.id
            item.stock_name_snapshot = payload.get("stock_name") or self._stock_name(code)
            item.source_json = payload["sources"]
            item.monitor_profile = payload["monitor_profile"]
            item.priority = payload["priority"]
            item.plan_id = payload.get("plan_id")
            item.position_snapshot_id = payload.get("position_snapshot_id")
            item.valid_until = _parse_datetime(payload.get("valid_until"))
            item.active, item.paused = True, False
            self.session.flush()
            self._ensure_default_rules(item, payload)
        row.pool_version, row.pool_hash, row.stock_count = version, preview["pool_hash"], len(incoming)
        row.status = "READY" if row.status == "DRAFT" and incoming else row.status
        if source_midday_run_id:
            row.source_run_ids_json = {**(row.source_run_ids_json or {}), "midday": source_midday_run_id}
        self.session.commit()
        return {**preview, "pool_version": version, "pool_version_id": pool_version.id, "confirmed": True}

    def list_pool(self, session_id: int, *, page: int, page_size: int) -> dict[str, Any]:
        query = select(IntradayMonitorItem).where(IntradayMonitorItem.monitor_session_id == session_id, IntradayMonitorItem.active.is_(True)).order_by(IntradayMonitorItem.priority, IntradayMonitorItem.stock_code)
        total = self.session.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = self.session.scalars(query.offset((page - 1) * page_size).limit(page_size)).all()
        return _page([item_dict(row) for row in rows], total, page, page_size)

    def update_item(self, item_id: int, updates: dict[str, Any]) -> dict[str, Any]:
        row = self.session.get(IntradayMonitorItem, item_id)
        if row is None: raise MonitorDomainError("MONITOR_ITEM_NOT_FOUND")
        for key in ("monitor_profile", "priority", "paused", "muted_until", "valid_until"):
            if key in updates and updates[key] is not None:
                value = updates[key]
                if key in {"muted_until", "valid_until"}: value = _parse_datetime(value)
                setattr(row, key, value)
        self.session.commit()
        return item_dict(row)

    def remove_item(self, item_id: int) -> None:
        row = self.session.get(IntradayMonitorItem, item_id)
        if row is None: raise MonitorDomainError("MONITOR_ITEM_NOT_FOUND")
        row.active = False
        session_row = self._session(row.monitor_session_id)
        session_row.stock_count = max(0, session_row.stock_count - 1)
        self.session.commit()

    def add_rule(self, item_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        item = self.session.get(IntradayMonitorItem, item_id)
        if item is None: raise MonitorDomainError("MONITOR_ITEM_NOT_FOUND")
        rule = IntradayMonitorRule(
            monitor_item_id=item_id, rule_type=payload["rule_type"], threshold_json=payload["threshold_json"],
            comparison=payload["comparison"], severity=payload["severity"],
            cooldown_seconds=int(payload.get("cooldown_seconds") or self.config["alerts"]["default_cooldown_seconds"]),
            consecutive_hits_required=int(payload.get("consecutive_hits_required") or self.config["alerts"]["require_consecutive_hits"]),
            hysteresis_percent=float(payload.get("hysteresis_percent") or self.config["alerts"]["hysteresis_percent"]),
            enabled=True, rule_version="1.0", source=payload.get("source", "CUSTOM"),
        )
        self.session.add(rule)
        self.session.commit()
        return rule_dict(rule)

    def list_rules(self, session_id: int) -> list[dict[str, Any]]:
        item_ids = select(IntradayMonitorItem.id).where(IntradayMonitorItem.monitor_session_id == session_id)
        rows = self.session.scalars(select(IntradayMonitorRule).where(IntradayMonitorRule.monitor_item_id.in_(item_ids)).order_by(IntradayMonitorRule.id)).all()
        return [rule_dict(row) for row in rows]

    def delete_rule(self, rule_id: int) -> None:
        row = self.session.get(IntradayMonitorRule, rule_id)
        if row is None: raise MonitorDomainError("MONITOR_RULE_NOT_FOUND")
        row.enabled = False
        self.session.commit()

    def list_alerts(self, session_id: int, *, page: int, page_size: int, severity: str | None = None, status: str | None = None) -> dict[str, Any]:
        query = select(IntradayMonitorAlert).where(IntradayMonitorAlert.monitor_session_id == session_id)
        if severity: query = query.where(IntradayMonitorAlert.severity == severity)
        if status: query = query.where(IntradayMonitorAlert.status == status)
        query = query.order_by(IntradayMonitorAlert.triggered_at.desc())
        total = self.session.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = self.session.scalars(query.offset((page - 1) * page_size).limit(page_size)).all()
        return _page([alert_dict(row) for row in rows], total, page, page_size)

    def unread_counts(self, session_id: int) -> dict[str, int]:
        rows = self.session.execute(select(IntradayMonitorAlert.severity, func.count()).where(
            IntradayMonitorAlert.monitor_session_id == session_id,
            IntradayMonitorAlert.status == "TRIGGERED",
        ).group_by(IntradayMonitorAlert.severity)).all()
        values = {severity: count for severity, count in rows}
        return {"unread": sum(values.values()), "warning": int(values.get("WARNING", 0)), "critical": int(values.get("CRITICAL", 0))}

    def alert_action(self, alert_id: int, action: str, *, operated_by: str, note: str | None = None, mute_minutes: int | None = None) -> dict[str, Any]:
        row = self.session.get(IntradayMonitorAlert, alert_id)
        if row is None: raise MonitorDomainError("MONITOR_ALERT_NOT_FOUND")
        previous = row.status
        mapping = {"ACKNOWLEDGE": "ACKNOWLEDGED", "RESOLVE": "RESOLVED", "DISMISS": "DISMISSED_FOR_DAY"}
        if action == "MUTE":
            row.status = "MUTED_UNTIL"
            item = self.session.get(IntradayMonitorItem, row.monitor_item_id)
            if item: item.muted_until = datetime.now(timezone.utc) + timedelta(minutes=mute_minutes or 15)
        else:
            row.status = mapping[action]
        if action == "ACKNOWLEDGE":
            row.acknowledged_at, row.acknowledged_by = datetime.now(timezone.utc), operated_by
        if action in {"RESOLVE", "DISMISS"}: row.resolution = note or action
        self.session.add(IntradayMonitorAlertAction(
            alert_id=row.id, action=action, operated_at=datetime.now(timezone.utc), operated_by=operated_by,
            note=note, previous_status=previous, new_status=row.status,
        ))
        self.session.commit()
        return alert_dict(row)

    def midday_suggestions(self, trade_date: date) -> dict[str, Any]:
        run = self.session.scalars(select(MiddayRecommendationRun).where(
            MiddayRecommendationRun.session_trade_date == trade_date,
            MiddayRecommendationRun.status.in_(["COMPLETED", "PARTIAL_SUCCESS"]),
        ).order_by(MiddayRecommendationRun.completed_at.desc()).limit(1)).first()
        if not run: return {"run_id": None, "items": [], "count": 0}
        rows = self.session.scalars(select(MiddayRecommendationResult).where(
            MiddayRecommendationResult.run_id == run.run_id,
        ).order_by(MiddayRecommendationResult.pro_rank.nulls_last(), MiddayRecommendationResult.midday_enhanced_score.desc()).limit(30)).all()
        items = [{"stock_code": row.stock_code, "stock_name": row.stock_name, "source": "MIDDAY_RECOMMENDATION", "priority": "NORMAL", "monitor_profile": "CANDIDATE_MONITOR", "recommended_price": _number(row.recommended_price), "max_acceptable_price": _number(row.max_acceptable_price), "stop_loss": _number(row.stop_loss), "take_profit_1": _number(row.take_profit_1), "take_profit_2": _number(row.take_profit_2)} for row in rows]
        return {"run_id": run.run_id, "items": items, "count": len(items)}

    def _normalize_items(self, items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for raw in items:
            code = normalize_ts_code(raw["stock_code"])
            profile = raw.get("monitor_profile", "CANDIDATE_MONITOR")
            priority = raw.get("priority", "NORMAL")
            if profile not in VALID_PROFILES: raise MonitorDomainError("INVALID_MONITOR_PROFILE")
            if priority not in VALID_PRIORITIES: raise MonitorDomainError("INVALID_MONITOR_PRIORITY")
            if profile == "POSITION_RISK_MONITOR":
                position = self.session.scalar(select(Position).where(Position.stock_code == code, Position.quantity > 0).order_by(Position.updated_at.desc()))
                truth_position = self.session.scalar(select(TraderPositionSnapshot).where(
                    TraderPositionSnapshot.stock_code == code,
                    TraderPositionSnapshot.quantity > 0,
                    TraderPositionSnapshot.is_current.is_(True),
                ).order_by(TraderPositionSnapshot.updated_at.desc()))
                if position is None and truth_position is None:
                    raise MonitorDomainError("CONFIRMED_POSITION_REQUIRED")
                raw = {**raw, "position_snapshot_id": raw.get("position_snapshot_id") or (position.id if position else truth_position.id)}
            if profile == "ORDER_PLAN_MONITOR" and not raw.get("plan_id"):
                raise MonitorDomainError("ACTIVE_ORDER_PLAN_REQUIRED")
            item = result.setdefault(code, {**raw, "stock_code": code, "sources": []})
            sources = raw.get("sources") or [raw.get("source", "DIRECT_SEARCH")]
            item["sources"] = sorted(set(item["sources"]) | {str(value) for value in sources})
        return result

    def _stock_name(self, code: str) -> str | None:
        row = self.session.scalar(select(StockMaster).where(StockMaster.code == code))
        return row.name if row else None

    def _ensure_default_rules(self, item: IntradayMonitorItem, payload: dict[str, Any]) -> None:
        existing = set(self.session.scalars(select(IntradayMonitorRule.rule_type).where(IntradayMonitorRule.monitor_item_id == item.id)).all())
        values = {
            "max_acceptable_price": payload.get("max_acceptable_price"),
            "stop_loss": payload.get("stop_loss"),
            "take_profit_1": payload.get("take_profit_1"),
            "take_profit_2": payload.get("take_profit_2"),
        }
        if item.plan_id:
            plan = self.session.get(OrderPlan, item.plan_id)
            if plan:
                values.update({
                    "max_acceptable_price": values["max_acceptable_price"] or _number(plan.max_acceptable_price),
                    "stop_loss": values["stop_loss"] or _number(plan.stop_loss_price),
                    "take_profit_1": values["take_profit_1"] or _number(plan.take_profit_1_price),
                    "take_profit_2": values["take_profit_2"] or _number(plan.take_profit_2_price),
                })
        definitions = [
            ("ABOVE_MAX_ACCEPTABLE_PRICE", values["max_acceptable_price"], ">", "WARNING", "超过最高接受价"),
            ("STOP_LOSS_BREACHED", values["stop_loss"], "<=", "CRITICAL", "止损条件触发"),
            ("TAKE_PROFIT_1_REACHED", values["take_profit_1"], ">=", "NOTICE", "达到止盈1"),
            ("TAKE_PROFIT_2_REACHED", values["take_profit_2"], ">=", "WARNING", "达到止盈2"),
            ("DATA_STALE", 120, ">", "WARNING", "数据已过期"),
        ]
        for rule_type, value, comparison, severity, label in definitions:
            if value is None or rule_type in existing:
                continue
            if rule_type.startswith(("STOP_LOSS", "TAKE_PROFIT")) and item.monitor_profile != "POSITION_RISK_MONITOR":
                continue
            self.session.add(IntradayMonitorRule(
                monitor_item_id=item.id, rule_type=rule_type,
                threshold_json={"value": value, "label": label}, comparison=comparison, severity=severity,
                cooldown_seconds=60 if severity == "CRITICAL" else int(self.config["alerts"]["default_cooldown_seconds"]),
                consecutive_hits_required=int(self.config["alerts"]["require_consecutive_hits"]),
                hysteresis_percent=float(self.config["alerts"]["hysteresis_percent"]), enabled=True,
                rule_version="1.0", source="PROFILE_TEMPLATE",
            ))

    def _items(self, session_id: int, *, include_inactive: bool = False) -> list[IntradayMonitorItem]:
        query = select(IntradayMonitorItem).where(IntradayMonitorItem.monitor_session_id == session_id)
        if not include_inactive: query = query.where(IntradayMonitorItem.active.is_(True))
        return list(self.session.scalars(query).all())

    def _session(self, session_id: int) -> IntradayMonitorSession:
        row = self.session.get(IntradayMonitorSession, session_id)
        if row is None: raise MonitorDomainError("MONITOR_SESSION_NOT_FOUND")
        return row


def session_dict(row: IntradayMonitorSession | None) -> dict[str, Any] | None:
    if row is None: return None
    return {key: getattr(row, key) for key in ("id", "trade_date", "status", "started_at", "paused_at", "resumed_at", "stopped_at", "market_session", "pool_version", "pool_hash", "stock_count", "external_call_count", "cache_hit_count", "alert_count", "critical_alert_count", "created_at", "updated_at")}


def item_dict(row: IntradayMonitorItem) -> dict[str, Any]:
    return {key: getattr(row, key) for key in ("id", "monitor_session_id", "pool_version_id", "stock_code", "stock_name_snapshot", "source_json", "monitor_profile", "priority", "plan_id", "position_snapshot_id", "active", "paused", "muted_until", "valid_from", "valid_until", "updated_at")}


def rule_dict(row: IntradayMonitorRule) -> dict[str, Any]:
    return {key: getattr(row, key) for key in ("id", "monitor_item_id", "rule_type", "threshold_json", "comparison", "severity", "cooldown_seconds", "consecutive_hits_required", "hysteresis_percent", "enabled", "rule_version", "source")}


def alert_dict(row: IntradayMonitorAlert) -> dict[str, Any]:
    return {key: getattr(row, key) for key in ("id", "monitor_session_id", "monitor_item_id", "rule_id", "stock_code", "triggered_at", "severity", "title", "message", "current_value_json", "threshold_json", "provider_time", "freshness_status", "status", "occurrence_count", "acknowledged_at", "acknowledged_by", "resolution")}


def _pool_hash(items: dict[str, dict[str, Any]]) -> str:
    value = "|".join(f"{code}:{','.join(item['sources'])}:{item['monitor_profile']}:{item['priority']}" for code, item in sorted(items.items()))
    return hashlib.sha256(value.encode()).hexdigest()


def _page(items: list[dict[str, Any]], total: int, page: int, page_size: int) -> dict[str, Any]:
    return {"items": items, "total": int(total), "page": page, "page_size": page_size, "total_pages": (int(total) + page_size - 1) // page_size if total else 0}


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""): return None
    if isinstance(value, datetime): return value
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _number(value: Any) -> float | None:
    return float(value) if value is not None else None
