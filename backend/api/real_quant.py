from __future__ import annotations

from fastapi import APIRouter, Request
from datetime import datetime
from pydantic import BaseModel, Field
from sqlalchemy import select

from backend.core.responses import error_response, success_response
from scripts.run_real_quant_top500 import run_real_quant_top500
from database.models.quant_run import QuantRankResult, QuantRun
from database.session import get_session, init_db
from quant.run_repository import QuantRunRepository
from temporal.readiness import DataReadinessService
from temporal.schemas import RunMode, TemporalStatus


router = APIRouter(prefix="/api/pools", tags=["manual-real-quant"])


class RealQuantRunRequest(BaseModel):
    provider: str = Field(default="tushare", pattern="^(mock|akshare|baostock|tushare)$")
    history_provider: str = Field(default="tushare", pattern="^(mock|akshare|baostock|tushare)$")
    backup_history_provider: str | None = Field(default="baostock", pattern="^(mock|akshare|baostock|tushare)$")
    top_n: int = Field(default=500, ge=1, le=1000)
    sample_limit: int = Field(default=0, ge=0)
    start_date: str | None = None
    end_date: str | None = None
    trade_date: str | None = None
    target_trade_date: str | None = None
    max_lookback_days: int = Field(default=15, ge=0, le=60)
    akshare_no_proxy: bool = False
    save_to_db: bool = False
    output: str = "data/reports/real_quant_top500_report.json"
    use_cache: bool = True
    refresh_cache: bool = False
    data_fetch_workers: int = Field(default=1, ge=1, le=8)
    factor_workers: str = "1"
    run_mode: RunMode | None = None
    decision_time: datetime | None = None
    allow_provisional: bool = False
    dry_run: bool = False


@router.post("/run-quant-real")
def run_quant_real(body: RealQuantRunRequest, request: Request) -> dict:
    try:
        temporal_context = temporal_manifest = None
        if body.run_mode is not None:
            temporal_context, temporal_manifest = DataReadinessService().check(
                body.run_mode, body.decision_time,
                base_market_trade_date=(datetime.strptime(body.trade_date, "%Y-%m-%d").date() if body.trade_date else None),
                target_trade_date=(datetime.strptime(body.target_trade_date, "%Y-%m-%d").date() if body.target_trade_date else None),
                allow_provisional=body.allow_provisional,
            )
            if body.dry_run:
                return success_response(
                    data=DataReadinessService.payload(temporal_context, temporal_manifest),
                    trace_id=request.state.trace_id,
                )
            if temporal_manifest.temporal_status is TemporalStatus.BLOCKED or not temporal_manifest.actionable:
                return error_response(
                    code="TEMPORAL_CONSISTENCY_BLOCKED",
                    message="Quant run blocked by temporal consistency gate.",
                    data=DataReadinessService.payload(temporal_context, temporal_manifest),
                    trace_id=request.state.trace_id,
                )
        report = run_real_quant_top500(
            provider=body.provider,
            history_provider=body.history_provider,
            backup_history_provider=body.backup_history_provider,
            top_n=body.top_n,
            sample_limit=body.sample_limit,
            start_date=body.start_date,
            end_date=body.end_date,
            trade_date=(temporal_context.base_market_trade_date.isoformat() if temporal_context else body.trade_date),
            max_lookback_days=body.max_lookback_days,
            akshare_no_proxy=body.akshare_no_proxy,
            save_to_db=body.save_to_db,
            output=body.output,
            progress=False,
            use_cache=body.use_cache,
            refresh_cache=body.refresh_cache,
            data_fetch_workers=body.data_fetch_workers,
            factor_workers=body.factor_workers,
        )
        quant_run = None
        if temporal_context and temporal_manifest:
            init_db(); session = get_session()
            try:
                quant_run = QuantRunRepository(session).save_report(
                    report, run_mode=body.run_mode.value, decision_time=temporal_context.decision_time,
                    base_trade_date=temporal_context.base_market_trade_date,
                    target_trade_date=temporal_context.target_trade_date,
                    manifest_id=temporal_manifest.id,
                    temporal_status=temporal_manifest.temporal_status.value,
                    actionable=temporal_manifest.actionable,
                    config_snapshot={"top_n": body.top_n, "provider": body.provider, "history_provider": body.history_provider},
                )
            finally: session.close()
        return success_response(
            data={
                "quant_run_id": quant_run.run_id if quant_run else None,
                "run_data_manifest_id": temporal_manifest.id if temporal_manifest else None,
                "temporal_status": temporal_manifest.temporal_status.value if temporal_manifest else None,
                "report_path": report["report_path"],
                "provider": report["provider"],
                "history_provider": report["history_provider"],
                "backup_history_provider": report["backup_history_provider"],
                "quant_mode": report["quant_mode"],
                "fallback_used": report["fallback_used"],
                "fallback_reason": report["fallback_reason"],
                "trade_date_cache_used": report.get("trade_date_cache_used", False),
                "trade_date_cache_hit_count": report.get("trade_date_cache_hit_count", 0),
                "trade_date_cache_miss_count": report.get("trade_date_cache_miss_count", 0),
                "per_stock_api_call_count": report.get("per_stock_api_call_count", 0),
                "baostock_backup_used_count": report.get("baostock_backup_used_count", 0),
                "requested_trade_date": report["requested_trade_date"],
                "actual_trade_date": report["actual_trade_date"],
                "baostock_date_attempts": report["baostock_date_attempts"],
                "akshare_proxy_mode": report["akshare_proxy_mode"],
                "akshare_proxy_env_detected": report["akshare_proxy_env_detected"],
                "universe_count": report["universe_count"],
                "filtered_count": report["filtered_count"],
                "scored_count": report["scored_count"],
                "top_count": report["top_count"],
                "factor_data_coverage": report["factor_data_coverage"],
                "tushare_api_success_count": report.get("tushare_api_success_count", 0),
                "tushare_api_empty_count": report.get("tushare_api_empty_count", 0),
                "tushare_api_error_count": report.get("tushare_api_error_count", 0),
                "skipped_count": report["skipped_count"],
                "failed_count": report["failed_count"],
                "warnings": report["warnings"],
                "performance": report.get("performance", {}),
                "top_stocks": report["top_stocks"],
                "no_llm_call_verified": report["no_llm_call_verified"],
            },
            trace_id=request.state.trace_id,
        )
    except Exception as exc:
        return error_response(
            code="REAL_QUANT_RUN_FAILED",
            message=str(exc),
            data={"provider": body.provider, "history_provider": body.history_provider},
            trace_id=request.state.trace_id,
        )


@router.get("/quant-runs/latest")
def latest_quant_run(request: Request) -> dict:
    init_db(); session = get_session()
    try:
        row = QuantRunRepository(session).latest_actionable()
        if row is None:
            return error_response("QUANT_RUN_NOT_FOUND", "No completed actionable quant run exists.", trace_id=request.state.trace_id)
        return success_response(data=_quant_run_payload(row), trace_id=request.state.trace_id)
    finally: session.close()


@router.get("/quant-runs/{run_id}")
def get_quant_run(run_id: str, request: Request) -> dict:
    session = get_session()
    try:
        row = session.scalar(select(QuantRun).where(QuantRun.run_id == run_id))
        if row is None: return error_response("QUANT_RUN_NOT_FOUND", "Quant run was not found.", trace_id=request.state.trace_id)
        return success_response(data=_quant_run_payload(row), trace_id=request.state.trace_id)
    finally: session.close()


@router.get("/quant-runs/{run_id}/ranking")
def get_quant_ranking(run_id: str, request: Request) -> dict:
    session = get_session()
    try:
        rows = session.scalars(select(QuantRankResult).where(QuantRankResult.quant_run_id == run_id).order_by(QuantRankResult.rank)).all()
        return success_response(data={"run_id": run_id, "count": len(rows), "ranking": [{"rank": row.rank, "stock_code": row.stock_code, "total_score": str(row.total_score), "technical_score": str(row.technical_score), "capital_score": str(row.capital_score), "emotion_score": str(row.emotion_score), "momentum_score": str(row.momentum_score), "risk_score": str(row.risk_score)} for row in rows]}, trace_id=request.state.trace_id)
    finally: session.close()


def _quant_run_payload(row: QuantRun) -> dict:
    return {key: getattr(row, key) for key in ("run_id", "run_mode", "decision_time", "base_market_trade_date", "target_trade_date", "factor_version", "data_manifest_id", "universe_count", "filtered_count", "scored_count", "skipped_count", "top_count", "no_llm_call_verified", "trade_date_cache_used", "per_stock_api_call_count", "total_seconds", "temporal_status", "actionable", "status")}
