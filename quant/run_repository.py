from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models.quant_run import QuantRankResult, QuantRun


class QuantRunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def save_report(self, report: dict, *, run_mode: str, decision_time: datetime, base_trade_date, target_trade_date, manifest_id: str, temporal_status: str, actionable: bool, config_snapshot: dict) -> QuantRun:
        hash_material = {"run_mode": run_mode, "decision_time": decision_time.isoformat(), "base": str(base_trade_date), "target": str(target_trade_date), "manifest": manifest_id, "config": config_snapshot}
        request_hash = hashlib.sha256(json.dumps(hash_material, sort_keys=True).encode()).hexdigest()
        existing = self.session.scalar(select(QuantRun).where(QuantRun.request_hash == request_hash))
        if existing:
            self._replace_rank_results(existing, report, base_trade_date)
            return existing
        run_id = str(report.get("run_id") or f"quant-{request_hash[:24]}")
        performance = report.get("performance", {})
        row = QuantRun(
            run_id=run_id, request_hash=request_hash, run_mode=run_mode, decision_time=decision_time,
            base_market_trade_date=base_trade_date, target_trade_date=target_trade_date,
            factor_version=report.get("factor_version"), config_snapshot=config_snapshot,
            data_manifest_id=manifest_id, universe_count=report.get("universe_count", 0),
            filtered_count=report.get("filtered_count", 0), scored_count=report.get("scored_count", 0),
            skipped_count=report.get("skipped_count", 0), top_count=report.get("top_count", 0),
            no_llm_call_verified=bool(report.get("no_llm_call_verified", False)),
            trade_date_cache_used=bool(report.get("trade_date_cache_used", False)),
            per_stock_api_call_count=int(report.get("per_stock_api_call_count", 0)),
            total_seconds=Decimal(str(performance.get("total_seconds") or performance.get("total_runtime_seconds") or 0)),
            temporal_status=temporal_status, actionable=actionable,
            status="COMPLETED" if report.get("no_llm_call_verified") else "FAILED",
        )
        self.session.add(row); self.session.flush()
        self._add_rank_results(row, report, base_trade_date)
        self.session.commit(); self.session.refresh(row); return row

    def _replace_rank_results(self, run: QuantRun, report: dict, base_trade_date) -> None:
        rows = report.get("all_scored_stocks") or report.get("top_stocks", [])
        if not rows or len(rows) <= self.session.query(QuantRankResult).filter(QuantRankResult.quant_run_id == run.run_id).count():
            return
        self.session.query(QuantRankResult).filter(QuantRankResult.quant_run_id == run.run_id).delete(synchronize_session=False)
        self._add_rank_results(run, report, base_trade_date)
        self.session.commit()

    def _add_rank_results(self, run: QuantRun, report: dict, base_trade_date) -> None:
        rows = report.get("all_scored_stocks") or report.get("top_stocks", [])
        for item in rows:
            self.session.add(QuantRankResult(
                quant_run_id=run.run_id, stock_code=item["stock_code"], rank=item["rank"],
                total_score=item.get("total_score", 0), technical_score=item.get("technical_score", 0),
                capital_score=item.get("capital_score", 0), emotion_score=item.get("emotion_score", 0),
                momentum_score=item.get("momentum_score", 0), risk_score=item.get("risk_score", 0),
                factor_detail_reference={
                    "stock_factor_score": {"stock_code": item["stock_code"], "date": str(base_trade_date)},
                    "stock_name": item.get("stock_name") or item["stock_code"],
                    "exchange": item.get("exchange") or _exchange(item["stock_code"]),
                    "level_one_sector": item.get("level_one_sector") or item.get("industry") or "UNKNOWN",
                    "classification_standard": item.get("classification_standard") or "TUSHARE_STOCK_BASIC_INDUSTRY",
                    "limit_status": item.get("limit_status") or "UNKNOWN",
                    "data_coverage_status": item.get("data_coverage_status") or "UNKNOWN",
                },
            ))

    def latest_actionable(self) -> QuantRun | None:
        return self.session.scalar(select(QuantRun).where(
            QuantRun.status == "COMPLETED", QuantRun.actionable.is_(True),
            QuantRun.temporal_status.in_(["PASS", "PASS_WITH_WARNINGS"]),
            QuantRun.no_llm_call_verified.is_(True), QuantRun.top_count >= 3,
        ).order_by(QuantRun.created_at.desc()))

    def samples(self, run_id: str, sample_size: int = 3) -> list[QuantRankResult]:
        rows = self.session.scalars(select(QuantRankResult).where(QuantRankResult.quant_run_id == run_id).order_by(QuantRankResult.rank)).all()
        if not rows: return []
        indexes = sorted(set([0, (len(rows) - 1) // 2, len(rows) - 1]))[:sample_size]
        return [rows[index] for index in indexes]


def _exchange(stock_code: str) -> str:
    code = str(stock_code).split(".", 1)[0]
    if code.startswith(("4", "8", "920")):
        return "BJ"
    return "SH" if code.startswith("6") else "SZ"
