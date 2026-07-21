from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from backend.core.config import get_app_config
from backend.core.responses import error_response, success_response
from database.models.ifind_shadow import MarketMinuteBarShadow, MarketSnapshotShadow
from database.models.intraday_monitor import IntradayMonitorAlert, IntradayMonitorItem, IntradayMonitorRefresh, IntradayMonitorRule
from database.session import get_session, init_db
from intraday_monitor.broker import market_data_broker
from intraday_monitor.config import load_monitor_config
from intraday_monitor.coordinator import IntradayMonitorCoordinator, _market_session
from intraday_monitor.service import IntradayMonitorService, MonitorDomainError, alert_dict, item_dict, rule_dict, session_dict
from services.ifind_shadow_service import IFindShadowService
from stock_codes import normalize_ts_code
from llm_gateway.schemas import LLMMessage, LLMRequest
from llm_gateway.service import LLMGatewayService


router = APIRouter(tags=["selected-stock-intraday-monitor"])


class SessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trade_date: date


class PoolItemInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stock_code: str = Field(min_length=6, max_length=32)
    stock_name: str | None = Field(default=None, max_length=128)
    source: str = Field(default="DIRECT_SEARCH", max_length=40)
    sources: list[str] = Field(default_factory=list, max_length=10)
    monitor_profile: Literal["CANDIDATE_MONITOR", "POSITION_RISK_MONITOR", "ORDER_PLAN_MONITOR", "CUSTOM_MONITOR"] = "CANDIDATE_MONITOR"
    priority: Literal["CRITICAL", "HIGH", "NORMAL", "LOW"] = "NORMAL"
    plan_id: int | None = None
    position_snapshot_id: int | None = None
    valid_until: datetime | None = None
    recommended_price: float | None = None
    max_acceptable_price: float | None = None
    stop_loss: float | None = None
    take_profit_1: float | None = None
    take_profit_2: float | None = None


class PoolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    monitor_session_id: int
    items: list[PoolItemInput] = Field(default_factory=list, max_length=50)
    source: str = Field(default="MANUAL_CONFIRMATION", max_length=64)
    source_midday_run_id: str | None = Field(default=None, max_length=64)
    confirmed_by: str = Field(default="LOCAL_TRADER", max_length=64)


class ItemUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    monitor_profile: Literal["CANDIDATE_MONITOR", "POSITION_RISK_MONITOR", "ORDER_PLAN_MONITOR", "CUSTOM_MONITOR"] | None = None
    priority: Literal["CRITICAL", "HIGH", "NORMAL", "LOW"] | None = None
    paused: bool | None = None
    muted_until: datetime | None = None
    valid_until: datetime | None = None


class RuleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    monitor_item_id: int
    rule_type: str = Field(min_length=2, max_length=64)
    threshold_json: dict[str, Any]
    comparison: Literal[">", ">=", "<", "<=", "=="]
    severity: Literal["INFO", "NOTICE", "WARNING", "CRITICAL"]
    cooldown_seconds: int | None = Field(default=None, ge=10, le=86400)
    consecutive_hits_required: int | None = Field(default=None, ge=1, le=10)
    hysteresis_percent: float | None = Field(default=None, ge=0, le=20)
    source: str = Field(default="CUSTOM", max_length=32)


class AlertActionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operated_by: str = Field(default="LOCAL_TRADER", max_length=64)
    note: str | None = Field(default=None, max_length=500)
    minutes: int | None = Field(default=15, ge=1, le=1440)


def _open():
    init_db()
    session = get_session()
    config = load_monitor_config(get_app_config())
    return session, IntradayMonitorService(session, config), config


def _domain(call, request: Request):
    try:
        return success_response(data=call(), trace_id=request.state.trace_id)
    except (MonitorDomainError, ValueError) as exc:
        code = str(exc).split(":", 1)[0]
        return error_response(code, str(exc), trace_id=request.state.trace_id)


@router.post("/api/workbench/intraday-monitor/sessions")
def create_session(body: SessionCreate, request: Request) -> dict:
    session, service, _ = _open()
    try: return _domain(lambda: session_dict(service.create_session(body.trade_date)), request)
    finally: session.close()


@router.post("/api/workbench/intraday-monitor/sessions/{session_id}/{action}")
def transition_session(session_id: int, action: Literal["start", "pause", "resume", "stop"], request: Request) -> dict:
    session, service, _ = _open()
    try:
        market_session = _market_session(datetime.now().astimezone())
        return _domain(lambda: session_dict(service.transition(session_id, action, market_session=market_session)), request)
    finally: session.close()


@router.get("/api/workbench/intraday-monitor/sessions/current")
def current_session(request: Request, trade_date: date | None = None) -> dict:
    session, service, _ = _open()
    try: return success_response(data=session_dict(service.current(trade_date)), trace_id=request.state.trace_id)
    finally: session.close()


@router.get("/api/workbench/intraday-monitor/sessions/history")
def session_history(request: Request, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)) -> dict:
    session, service, _ = _open()
    try: return success_response(data=service.history(page=page, page_size=page_size), trace_id=request.state.trace_id)
    finally: session.close()


@router.post("/api/workbench/intraday-monitor/pool/preview")
def preview_pool(body: PoolRequest, request: Request) -> dict:
    session, service, _ = _open()
    try: return _domain(lambda: service.preview_pool(body.monitor_session_id, [item.model_dump(mode="json") for item in body.items]), request)
    finally: session.close()


@router.post("/api/workbench/intraday-monitor/pool/confirm")
def confirm_pool(body: PoolRequest, request: Request) -> dict:
    session, service, _ = _open()
    try:
        return _domain(lambda: service.confirm_pool(body.monitor_session_id, [item.model_dump(mode="json") for item in body.items], source=body.source, confirmed_by=body.confirmed_by, source_midday_run_id=body.source_midday_run_id), request)
    finally: session.close()


@router.post("/api/workbench/intraday-monitor/pool/items")
def add_pool_item(body: PoolRequest, request: Request) -> dict:
    return confirm_pool(body, request)


@router.post("/api/workbench/intraday-monitor/pool/items/batch")
def add_pool_items(body: PoolRequest, request: Request) -> dict:
    return confirm_pool(body, request)


@router.put("/api/workbench/intraday-monitor/pool/items/{item_id}")
def update_pool_item(item_id: int, body: ItemUpdate, request: Request) -> dict:
    session, service, _ = _open()
    try: return _domain(lambda: service.update_item(item_id, body.model_dump(exclude_none=True, mode="json")), request)
    finally: session.close()


@router.delete("/api/workbench/intraday-monitor/pool/items/{item_id}")
def delete_pool_item(item_id: int, request: Request) -> dict:
    session, service, _ = _open()
    try:
        def operation(): service.remove_item(item_id); return {"removed": True, "item_id": item_id}
        return _domain(operation, request)
    finally: session.close()


@router.get("/api/workbench/intraday-monitor/pool")
def list_pool(request: Request, monitor_session_id: int, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100)) -> dict:
    session, service, _ = _open()
    try: return success_response(data=service.list_pool(monitor_session_id, page=page, page_size=page_size), trace_id=request.state.trace_id)
    finally: session.close()


@router.get("/api/workbench/intraday-monitor/midday-suggestions")
def midday_suggestions(request: Request, trade_date: date) -> dict:
    session, service, _ = _open()
    try: return success_response(data=service.midday_suggestions(trade_date), trace_id=request.state.trace_id)
    finally: session.close()


@router.post("/api/workbench/intraday-monitor/midday-suggestions/preview")
def preview_midday(body: PoolRequest, request: Request) -> dict:
    return preview_pool(body, request)


@router.post("/api/workbench/intraday-monitor/midday-suggestions/confirm")
def confirm_midday(body: PoolRequest, request: Request) -> dict:
    return confirm_pool(body, request)


@router.get("/api/workbench/intraday-monitor/rules")
def list_rules(request: Request, monitor_session_id: int) -> dict:
    session, service, _ = _open()
    try: return success_response(data={"items": service.list_rules(monitor_session_id)}, trace_id=request.state.trace_id)
    finally: session.close()


@router.post("/api/workbench/intraday-monitor/rules")
def create_rule(body: RuleInput, request: Request) -> dict:
    session, service, _ = _open()
    try: return _domain(lambda: service.add_rule(body.monitor_item_id, body.model_dump(exclude_none=True)), request)
    finally: session.close()


@router.put("/api/workbench/intraday-monitor/rules/{rule_id}")
def update_rule(rule_id: int, body: RuleInput, request: Request) -> dict:
    session, _service, _ = _open()
    try:
        row = session.get(IntradayMonitorRule, rule_id)
        if row is None: return error_response("MONITOR_RULE_NOT_FOUND", "Rule not found", trace_id=request.state.trace_id)
        for key, value in body.model_dump(exclude_none=True).items():
            if key != "monitor_item_id": setattr(row, key, value)
        session.commit()
        return success_response(data=rule_dict(row), trace_id=request.state.trace_id)
    finally: session.close()


@router.delete("/api/workbench/intraday-monitor/rules/{rule_id}")
def delete_rule(rule_id: int, request: Request) -> dict:
    session, service, _ = _open()
    try:
        def operation(): service.delete_rule(rule_id); return {"disabled": True, "rule_id": rule_id}
        return _domain(operation, request)
    finally: session.close()


@router.post("/api/workbench/intraday-monitor/refresh")
def refresh_monitor(request: Request, monitor_session_id: int) -> dict:
    session, _service, config = _open()
    try:
        data = IntradayMonitorCoordinator(session, config, market_service=IFindShadowService(session, config=get_app_config())).tick(monitor_session_id)
        return success_response(data=data, trace_id=request.state.trace_id)
    finally: session.close()


@router.get("/api/workbench/intraday-monitor/results")
def monitor_results(request: Request, monitor_session_id: int, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100)) -> dict:
    session, _service, _ = _open()
    try:
        items = session.scalars(select(IntradayMonitorItem).where(IntradayMonitorItem.monitor_session_id == monitor_session_id, IntradayMonitorItem.active.is_(True)).order_by(IntradayMonitorItem.priority, IntradayMonitorItem.stock_code)).all()
        total, rows = len(items), items[(page - 1) * page_size:page * page_size]
        payload = [_result_row(session, item) for item in rows]
        return success_response(data={"items": payload, "total": total, "page": page, "page_size": page_size}, trace_id=request.state.trace_id)
    finally: session.close()


@router.get("/api/workbench/intraday-monitor/stocks/{stock_code}")
def stock_detail(stock_code: str, request: Request, monitor_session_id: int) -> dict:
    session, _service, _ = _open()
    try:
        code = normalize_ts_code(stock_code)
        item = session.scalar(select(IntradayMonitorItem).where(IntradayMonitorItem.monitor_session_id == monitor_session_id, IntradayMonitorItem.stock_code == code))
        if not item: return error_response("MONITOR_ITEM_NOT_FOUND", "Stock is not in the selected monitor pool", trace_id=request.state.trace_id)
        return success_response(data={**_result_row(session, item), "rules": [rule_dict(row) for row in session.scalars(select(IntradayMonitorRule).where(IntradayMonitorRule.monitor_item_id == item.id)).all()]}, trace_id=request.state.trace_id)
    finally: session.close()


@router.get("/api/workbench/intraday-monitor/stocks/{stock_code}/minute-bars")
def stock_minute_bars(stock_code: str, request: Request, trade_date: date | None = None) -> dict:
    session, _service, _ = _open()
    try:
        rows = IFindShadowService(session, config=get_app_config()).minute_bars(stock_code, trade_date=trade_date)
        return success_response(data={"items": rows, "count": len(rows), "industry_realtime_status": "NOT_AVAILABLE", "concept_realtime_status": "NOT_AVAILABLE"}, trace_id=request.state.trace_id)
    finally: session.close()


@router.get("/api/workbench/intraday-monitor/alerts")
def list_alerts(request: Request, monitor_session_id: int, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100), severity: str | None = None, alert_status: str | None = None) -> dict:
    session, service, _ = _open()
    try: return success_response(data=service.list_alerts(monitor_session_id, page=page, page_size=page_size, severity=severity, status=alert_status), trace_id=request.state.trace_id)
    finally: session.close()


@router.get("/api/workbench/intraday-monitor/alerts/unread-count")
def unread_count(request: Request, monitor_session_id: int) -> dict:
    session, service, _ = _open()
    try: return success_response(data=service.unread_counts(monitor_session_id), trace_id=request.state.trace_id)
    finally: session.close()


def _alert_action(alert_id: int, action: str, body: AlertActionInput, request: Request) -> dict:
    session, service, _ = _open()
    try: return _domain(lambda: service.alert_action(alert_id, action, operated_by=body.operated_by, note=body.note, mute_minutes=body.minutes), request)
    finally: session.close()


@router.post("/api/workbench/intraday-monitor/alerts/{alert_id}/acknowledge")
def acknowledge_alert(alert_id: int, body: AlertActionInput, request: Request) -> dict: return _alert_action(alert_id, "ACKNOWLEDGE", body, request)


@router.post("/api/workbench/intraday-monitor/alerts/{alert_id}/mute")
def mute_alert(alert_id: int, body: AlertActionInput, request: Request) -> dict: return _alert_action(alert_id, "MUTE", body, request)


@router.post("/api/workbench/intraday-monitor/alerts/{alert_id}/resolve")
def resolve_alert(alert_id: int, body: AlertActionInput, request: Request) -> dict: return _alert_action(alert_id, "RESOLVE", body, request)


@router.post("/api/workbench/intraday-monitor/alerts/{alert_id}/dismiss")
def dismiss_alert(alert_id: int, body: AlertActionInput, request: Request) -> dict: return _alert_action(alert_id, "DISMISS", body, request)


@router.post("/api/workbench/intraday-monitor/alerts/{alert_id}/explain")
def explain_alert(alert_id: int, request: Request) -> dict:
    session, _service, _ = _open()
    try:
        alert = session.get(IntradayMonitorAlert, alert_id)
        if alert is None:
            return error_response("MONITOR_ALERT_NOT_FOUND", "Alert not found", trace_id=request.state.trace_id)
        schema = {
            "type": "object", "additionalProperties": False,
            "required": ["decision", "explanation", "risk_notes"],
            "properties": {
                "decision": {"type": "string", "enum": ["CONFIRM_ALERT", "MORE_CONSERVATIVE", "MANUAL_REVIEW", "DATA_INSUFFICIENT"]},
                "explanation": {"type": "string"},
                "risk_notes": {"type": "array", "items": {"type": "string"}},
            },
        }
        payload = {
            "stock_code": alert.stock_code, "severity": alert.severity, "rule": alert.title,
            "message": alert.message, "current_value": alert.current_value_json,
            "threshold": alert.threshold_json, "provider_time": alert.provider_time,
            "freshness": alert.freshness_status,
        }
        response = LLMGatewayService(db_session=session).chat(LLMRequest(
            agent_name="intraday_alert_explainer", task="normal_explanation", task_type="normal_explanation",
            messages=[
                LLMMessage(role="system", content="Explain the supplied rule alert for manual review. Return JSON only. Do not create prices, quantities, orders, URLs, or claim an action was executed."),
                LLMMessage(role="user", content=json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
            ], response_schema=schema, json_mode=True, thinking_mode="disabled", allow_fallback=True,
            metadata={"user_initiated": True, "alert_id": alert.id},
        ))
        data = response.structured_output or response.parsed_json or {"decision": "DATA_INSUFFICIENT", "explanation": "AI解释暂不可用，请按原始规则人工复核。", "risk_notes": []}
        return success_response(data={**data, "alert_id": alert.id, "gateway_status": response.status, "original_alert_unchanged": True, "orders_created": 0}, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/api/workbench/intraday-monitor/usage")
def monitor_usage(request: Request, monitor_session_id: int | None = None) -> dict:
    session, _service, _ = _open()
    try:
        query = select(IntradayMonitorRefresh).order_by(IntradayMonitorRefresh.started_at.desc())
        if monitor_session_id: query = query.where(IntradayMonitorRefresh.monitor_session_id == monitor_session_id)
        rows = session.scalars(query.limit(100)).all()
        return success_response(data={"items": [{key: getattr(row, key) for key in ("id", "monitor_session_id", "refresh_type", "priority", "started_at", "completed_at", "requested_codes", "returned_codes", "missing_codes", "external_calls", "cache_hits", "latency_ms", "status", "error_category")} for row in rows], "broker": market_data_broker.snapshot()}, trace_id=request.state.trace_id)
    finally: session.close()


@router.get("/api/workbench/intraday-monitor/events")
def monitor_events(monitor_session_id: int):
    def stream():
        yield f"event: monitor.session.status\ndata: {json.dumps({'monitor_session_id': monitor_session_id, 'recovery_poll_required': True})}\n\n"
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


def _result_row(session, item: IntradayMonitorItem) -> dict[str, Any]:
    snapshot = session.scalars(select(MarketSnapshotShadow).where(MarketSnapshotShadow.stock_code == item.stock_code).order_by(MarketSnapshotShadow.snapshot_time.desc()).limit(1)).first()
    alert = session.scalars(select(IntradayMonitorAlert).where(IntradayMonitorAlert.monitor_item_id == item.id).order_by(IntradayMonitorAlert.triggered_at.desc()).limit(1)).first()
    data = item_dict(item)
    if snapshot:
        data.update({key: getattr(snapshot, key) for key in ("latest", "change_percent", "provider_time", "data_status", "snapshot_time")})
    data.update({"current_alert_severity": alert.severity if alert else None, "current_alert": alert.title if alert else None, "last_alert_time": alert.triggered_at if alert else None, "alert_status": alert.status if alert else None})
    return data
