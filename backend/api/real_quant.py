from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from backend.core.responses import error_response, success_response
from scripts.run_real_quant_top500 import run_real_quant_top500


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
    max_lookback_days: int = Field(default=15, ge=0, le=60)
    akshare_no_proxy: bool = False
    save_to_db: bool = False
    output: str = "data/reports/real_quant_top500_report.json"
    use_cache: bool = True
    refresh_cache: bool = False
    data_fetch_workers: int = Field(default=1, ge=1, le=8)
    factor_workers: str = "1"


@router.post("/run-quant-real")
def run_quant_real(body: RealQuantRunRequest, request: Request) -> dict:
    try:
        report = run_real_quant_top500(
            provider=body.provider,
            history_provider=body.history_provider,
            backup_history_provider=body.backup_history_provider,
            top_n=body.top_n,
            sample_limit=body.sample_limit,
            start_date=body.start_date,
            end_date=body.end_date,
            trade_date=body.trade_date,
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
        return success_response(
            data={
                "report_path": report["report_path"],
                "provider": report["provider"],
                "history_provider": report["history_provider"],
                "backup_history_provider": report["backup_history_provider"],
                "quant_mode": report["quant_mode"],
                "fallback_used": report["fallback_used"],
                "fallback_reason": report["fallback_reason"],
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
