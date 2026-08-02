from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select

from backend.core.responses import success_response
from database.models.event_overlay import (
    EventEvidenceItemRecord,
    EventEvidenceSnapshotRecord,
    EventOverlayDataIssueRecord,
    EventScreeningItemRecord,
    EventScreeningRunRecord,
)
from database.session import get_session, init_db


router = APIRouter(prefix="/api/event-overlay", tags=["event-overlay-shadow-readonly"])


def _resolve_run(
    session,
    *,
    run_id: str | None,
    trade_date: date | None,
    screening_version: str,
) -> EventScreeningRunRecord:
    if not run_id and trade_date is None:
        raise HTTPException(status_code=422, detail="run_id or trade_date is required")
    query = select(EventScreeningRunRecord).where(
        EventScreeningRunRecord.screening_version == screening_version
    )
    if run_id:
        query = query.where(EventScreeningRunRecord.run_id == run_id)
    else:
        query = query.where(EventScreeningRunRecord.trade_date == trade_date).order_by(
            EventScreeningRunRecord.created_at.desc()
        )
    rows = list(session.scalars(query))
    row = next(
        (
            candidate
            for candidate in rows
            if (candidate.manifest_json or {}).get("final_status")
            == "V3_EVENT_OVERLAY_SHADOW_READY"
            and (
                not str(candidate.screening_version).startswith(
                    "LLM_SCREENING_V3_1_"
                )
                or (candidate.manifest_json or {}).get(
                    "database_publish_eligible"
                )
                is True
            )
        ),
        None,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="event overlay run not found")
    return row


def _session():
    init_db()
    return get_session()


@router.get("/runs")
def runs(
    request: Request,
    screening_version: str = Query(..., min_length=1, max_length=96),
    run_id: str | None = Query(default=None, max_length=64),
    trade_date: date | None = Query(default=None),
) -> dict:
    session = _session()
    try:
        row = _resolve_run(session, run_id=run_id, trade_date=trade_date, screening_version=screening_version)
        data = {
            "items": [{
                "run_id": row.run_id,
                "trade_date": row.trade_date,
                "decision_as_of_time": row.decision_as_of_time,
                "screening_version": row.screening_version,
                "factor_version": row.factor_version,
                "production_or_shadow": row.production_or_shadow,
                "execution_mode": row.execution_mode,
                "input_count": row.input_count,
                "output_count": row.output_count,
                "actual_network_calls": row.actual_network_calls,
            }]
        }
        return success_response(data=data, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/summary")
def summary(
    request: Request,
    screening_version: str = Query(...),
    run_id: str | None = Query(default=None),
    trade_date: date | None = Query(default=None),
) -> dict:
    session = _session()
    try:
        row = _resolve_run(session, run_id=run_id, trade_date=trade_date, screening_version=screening_version)
        return success_response(data=row.manifest_json, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/top20")
def top20(
    request: Request,
    screening_version: str = Query(...),
    run_id: str | None = Query(default=None),
    trade_date: date | None = Query(default=None),
) -> dict:
    session = _session()
    try:
        run = _resolve_run(session, run_id=run_id, trade_date=trade_date, screening_version=screening_version)
        rows = list(session.scalars(
            select(EventScreeningItemRecord)
            .where(
                EventScreeningItemRecord.screening_run_id == run.id,
                EventScreeningItemRecord.selected_top20.is_(True),
            )
            .order_by(EventScreeningItemRecord.v3_rank)
        ))
        return success_response(data={"items": [_item(row) for row in rows]}, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/comparison")
def comparison(
    request: Request,
    screening_version: str = Query(...),
    run_id: str | None = Query(default=None),
    trade_date: date | None = Query(default=None),
) -> dict:
    session = _session()
    try:
        run = _resolve_run(session, run_id=run_id, trade_date=trade_date, screening_version=screening_version)
        rows = list(session.scalars(
            select(EventScreeningItemRecord)
            .where(EventScreeningItemRecord.screening_run_id == run.id)
            .order_by(EventScreeningItemRecord.v3_rank)
        ))
        v2_codes = list(((run.manifest_json.get("forward_ab") or {}).get("group_a") or {}).get("stock_codes") or [])
        v2_rank = {code: index for index, code in enumerate(v2_codes, start=1)}
        data = [{
            **_item(row),
            "v2_screening_rank": v2_rank.get(row.stock_code),
            "rank_change": (
                v2_rank[row.stock_code] - row.v3_rank if row.stock_code in v2_rank else None
            ),
        } for row in rows]
        return success_response(data={"items": data}, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/evidence")
def evidence(
    request: Request,
    screening_version: str = Query(...),
    run_id: str | None = Query(default=None),
    trade_date: date | None = Query(default=None),
    stock_code: str | None = Query(default=None, min_length=6, max_length=32),
) -> dict:
    session = _session()
    try:
        run = _resolve_run(session, run_id=run_id, trade_date=trade_date, screening_version=screening_version)
        snapshot_query = select(EventEvidenceSnapshotRecord).where(EventEvidenceSnapshotRecord.run_id == run.run_id)
        if stock_code:
            snapshot_query = snapshot_query.where(EventEvidenceSnapshotRecord.stock_code == stock_code.split(".")[0])
        snapshots = list(session.scalars(snapshot_query.order_by(EventEvidenceSnapshotRecord.stock_code)))
        snapshot_ids = [row.id for row in snapshots]
        items = list(session.scalars(
            select(EventEvidenceItemRecord).where(EventEvidenceItemRecord.snapshot_id.in_(snapshot_ids))
        )) if snapshot_ids else []
        return success_response(data={
            "snapshots": [_snapshot(row) for row in snapshots],
            "items": [_evidence_item(row) for row in items],
        }, trace_id=request.state.trace_id)
    finally:
        session.close()


@router.get("/data-quality")
def data_quality(
    request: Request,
    screening_version: str = Query(...),
    run_id: str | None = Query(default=None),
    trade_date: date | None = Query(default=None),
) -> dict:
    session = _session()
    try:
        run = _resolve_run(session, run_id=run_id, trade_date=trade_date, screening_version=screening_version)
        rows = list(session.scalars(
            select(EventOverlayDataIssueRecord)
            .where(EventOverlayDataIssueRecord.run_id == run.run_id)
            .order_by(EventOverlayDataIssueRecord.stock_code)
        ))
        return success_response(data={"items": [{
            "issue_id": row.issue_id,
            "stock_code": row.stock_code,
            "issue_code": row.issue_code,
            "issue_level": row.issue_level,
            "detail": row.detail,
        } for row in rows]}, trace_id=request.state.trace_id)
    finally:
        session.close()


def _item(row: EventScreeningItemRecord) -> dict:
    return {
        "stock_code": row.stock_code,
        "stock_name": row.stock_name,
        "quant_rank": row.quant_rank,
        "quant_score": row.quant_score,
        "event_opportunity_score": row.event_opportunity_score,
        "evidence_confidence": row.evidence_confidence,
        "evidence_breadth": row.evidence_breadth,
        "risk_action": row.risk_action,
        "v3_screening_score": row.v3_screening_score,
        "v3_rank": row.v3_rank,
        "selected_top20": row.selected_top20,
        "search_status": row.search_status,
    }


def _snapshot(row: EventEvidenceSnapshotRecord) -> dict:
    return {
        "snapshot_id": row.snapshot_id,
        "stock_code": row.stock_code,
        "decision_as_of_time": row.decision_as_of_time,
        "search_status": row.search_status,
        "provider": row.provider,
        "provider_verified": row.provider_verified,
        "direct_search_used": row.direct_search_used,
        "confidence_discount_applied": row.confidence_discount_applied,
        "production_eligible": row.production_eligible,
        "shadow_eligible": row.shadow_eligible,
        "content_hash": row.content_hash,
    }


def _evidence_item(row: EventEvidenceItemRecord) -> dict:
    audit = row.raw_metadata_json or {}
    return {
        "event_id": row.event_id,
        "event_cluster_id": row.event_cluster_id,
        "event_type": row.event_type,
        "title": row.title,
        "summary": row.summary,
        "url": row.url,
        "domain": row.domain,
        "published_at": row.published_at,
        "source_tier": row.source_tier,
        "event_direction": row.event_direction,
        "confidence": row.confidence,
        "temporal_status": row.temporal_status,
        "score_eligible": audit.get("score_eligible"),
        "score_exclusion_reasons": audit.get("score_exclusion_reasons"),
        "freshness_window_hours": audit.get("freshness_window_hours"),
        "time_decay": audit.get("time_decay"),
    }
