from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select

from backend.core.config_manager import ConfigManager
from database.models.performance import (
    SelectionCohort,
    SelectionCohortMember,
    SelectionPerformanceDaily,
    SelectionPerformanceRun,
    SelectionPortfolioDaily,
)
from database.models.workbench import PipelineJob
from review.performance_cache import PerformanceCacheManager, stable_hash
from review.performance_calculators import PortfolioReturnCalculator, ReturnCalculator
from review.performance_cohorts import SelectionCohortResolver
from review.performance_market import MarketDataBatchLoader
from review.performance_schemas import MemberSnapshot, PerformanceRequest


ALGORITHM_VERSION = "selection-performance-v1"
SCHEMA_VERSION = "1.0"
TRADE_CALENDAR_VERSION = "local-batch-calendar-v1"
ACTIVE = {"PENDING", "RUNNING"}


class SelectionPerformanceService:
    def __init__(self, session, *, cache_manager: PerformanceCacheManager | None = None, market_loader: MarketDataBatchLoader | None = None) -> None:
        self.session = session
        self.cache = cache_manager or PerformanceCacheManager()
        self.market = market_loader or MarketDataBatchLoader(session)
        self.cohorts = SelectionCohortResolver(session)

    def settings(self) -> dict[str, Any]:
        manager = ConfigManager(session=self.session)
        values = manager.get_effective_config()["values"]
        prefix = "selection_performance."
        return {key.removeprefix(prefix): value for key, value in values.items() if key.startswith(prefix)}

    def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        manager = ConfigManager(session=self.session)
        manager.set_config_values_bulk(
            [{"config_key": f"selection_performance.{key}", "value": value} for key, value in values.items()],
            user="local_trader", reason="Selection performance settings update",
        )
        return self.settings()

    def start(self, request: PerformanceRequest) -> dict[str, Any]:
        settings = self.settings()
        request.validate(int(settings["max_lookback_value"]))
        cohorts = self.cohorts.resolve(request.start_selection_date, request.end_selection_date or request.evaluation_end_date, request.lookback_value)
        components, watermark = self._input_components(request, cohorts, settings)
        input_hash = stable_hash(components)
        existing = self.session.scalar(select(SelectionPerformanceRun).where(
            SelectionPerformanceRun.performance_input_hash == input_hash,
            SelectionPerformanceRun.status == "SUCCESS",
        ).order_by(SelectionPerformanceRun.completed_at.desc()))
        if existing and not request.force_recalculate:
            return {
                "job_id": None, "performance_run_id": existing.run_id, "existing_run_id": existing.run_id,
                "duplicate_status": "SUCCESS_CACHE_HIT", "cache_status": "SUCCESS",
            }
        active_jobs = self.session.scalars(select(PipelineJob).where(
            PipelineJob.job_type == "SELECTION_PERFORMANCE_REFRESH", PipelineJob.status.in_(ACTIVE)
        )).all()
        for job in active_jobs:
            if (job.checkpoint or {}).get("performance_input_hash") == input_hash:
                return {"job_id": job.job_id, "performance_run_id": None, "existing_run_id": None, "duplicate_status": "RUNNING", "cache_status": "RUNNING"}
        run_id = f"performance-{uuid.uuid4().hex[:20]}"
        job_id = f"workbench-{uuid.uuid4().hex[:20]}"
        config_snapshot = _json_safe({"request": request.snapshot(), "settings": settings, "input_components": components})
        run = SelectionPerformanceRun(
            run_id=run_id, performance_input_hash=input_hash, evaluation_end_date=request.evaluation_end_date,
            lookback_value=request.lookback_value, lookback_unit=request.lookback_unit,
            start_selection_date=request.start_selection_date, end_selection_date=request.end_selection_date,
            return_basis=request.return_basis, selection_scope=request.selection_scope, weighting_mode=request.weighting_mode,
            include_zero_position_stocks=request.include_zero_position_stocks,
            include_risk_blocked_stocks=request.include_risk_blocked_stocks, status="PENDING",
            algorithm_version=ALGORITHM_VERSION, schema_version=SCHEMA_VERSION,
            market_data_watermark_hash=watermark, trade_calendar_version=TRADE_CALENDAR_VERSION,
            config_snapshot_json=config_snapshot,
        )
        job = PipelineJob(
            job_id=job_id, job_type="SELECTION_PERFORMANCE_REFRESH", trade_date=request.evaluation_end_date,
            status="PENDING", stage="VALIDATING_INPUT", progress_current=0, progress_total=max(1, len(cohorts)),
            checkpoint={"performance_run_id": run_id, "performance_input_hash": input_hash, "incremental": False},
        )
        self.session.add_all([run, job])
        self.session.commit()
        return {"job_id": job_id, "performance_run_id": run_id, "existing_run_id": None, "duplicate_status": "CREATED", "cache_status": "PENDING"}

    def execute(self, run_id: str, job_id: str) -> dict[str, Any]:
        run = self._run(run_id)
        job = self._job(job_id)
        request = _request_from_snapshot(run.config_snapshot_json["request"])
        try:
            run.status, job.status, job.stage = "RUNNING", "RUNNING", "RESOLVING_COHORTS"
            job.started_at = datetime.now(timezone.utc)
            self.session.commit()
            cohorts = self.cohorts.resolve(request.start_selection_date, request.end_selection_date or request.evaluation_end_date, request.lookback_value)
            all_members: list[SelectionCohortMember] = []
            members_by_cohort: dict[int, list[SelectionCohortMember]] = {}
            for cohort in cohorts:
                members = self.cohorts.members(cohort, request.selection_scope, request.include_zero_position_stocks, request.include_risk_blocked_stocks)
                members_by_cohort[cohort.id] = members
                all_members.extend(members)
            market_dates = self.market.available_dates(request.evaluation_end_date)
            needed_dates = sorted(set(market_dates) | {cohort.selection_trade_date for cohort in cohorts})
            bars, watermark = self.market.load({row.stock_code for row in all_members}, needed_dates)
            run.market_data_watermark_hash = watermark
            job.stage = "CALCULATING_STOCK_RETURNS"
            self.session.commit()
            stock_payloads: list[dict[str, Any]] = []
            portfolio_payloads: list[dict[str, Any]] = []
            for index, cohort in enumerate(cohorts, start=1):
                members = members_by_cohort[cohort.id]
                snapshots = [MemberSnapshot(row.id, row.stock_code, cohort.selection_trade_date, float(row.suggested_position_percent or 0)) for row in members]
                evaluation_dates = [day for day in market_dates if cohort.selection_trade_date < day <= request.evaluation_end_date]
                cohort_stock_rows = []
                for member in snapshots:
                    rows = ReturnCalculator().calculate(member, bars.get(member.stock_code, {}), evaluation_dates, request.return_basis)
                    cohort_stock_rows.extend(rows)
                    stock_payloads.extend(rows)
                job.stage = "CALCULATING_PORTFOLIO_RETURNS"
                portfolio_payloads.extend(PortfolioReturnCalculator().calculate(cohort.id, snapshots, cohort_stock_rows, request.weighting_mode))
                job.progress_current, job.current_stock = index, None
                self.session.commit()
            job.stage = "PERSISTING_RESULTS"
            self._replace_results(run, stock_payloads, portfolio_payloads)
            expected = sum(
                len(members_by_cohort[cohort.id]) * len([day for day in market_dates if cohort.selection_trade_date < day <= request.evaluation_end_date])
                for cohort in cohorts
            )
            valid = sum(row["daily_return"] is not None for row in stock_payloads)
            coverage = valid / expected if expected else 0.0
            min_coverage = float(run.config_snapshot_json["settings"]["min_coverage_ratio"])
            run.status = "SUCCESS" if expected and coverage >= min_coverage else "PARTIAL_SUCCESS"
            run.cohort_count, run.stock_count = len(cohorts), len(all_members)
            run.daily_record_count, run.portfolio_record_count = len(stock_payloads), len(portfolio_payloads)
            run.coverage_ratio, run.completed_at = coverage, datetime.now(timezone.utc)
            job.status, job.stage = run.status, "COMPLETED"
            job.success_count, job.failure_count = valid, expected - valid
            job.progress_current, job.progress_total = len(cohorts), len(cohorts)
            job.finished_at = datetime.now(timezone.utc)
            job.checkpoint = {**(job.checkpoint or {}), "cache_status": run.status, "coverage_ratio": coverage, "per_stock_api_call_count": 0, "no_llm_call_verified": True}
            self.session.commit()
            self._write_cache(run)
            return self.run_detail(run.run_id)
        except Exception as exc:
            self.session.rollback()
            run = self._run(run_id)
            job = self._job(job_id)
            run.status, run.error_message = "FAILED", str(exc)[:500]
            job.status, job.stage, job.error_code, job.error_message = "FAILED", "FAILED", "PERFORMANCE_CALCULATION_FAILED", str(exc)[:500]
            job.finished_at = datetime.now(timezone.utc)
            self.session.commit()
            raise

    def incremental_refresh(self, run_id: str, evaluation_end_date: date) -> dict[str, Any]:
        old = self._run(run_id)
        if old.status not in {"SUCCESS", "PARTIAL_SUCCESS", "STALE"}:
            raise ValueError("PERFORMANCE_RUN_NOT_INCREMENTAL")
        if evaluation_end_date <= old.evaluation_end_date:
            return {"performance_run_id": old.run_id, "added_evaluation_dates": 0, "duplicate_count": 0, "status": "ALREADY_CURRENT"}
        new_dates = [day for day in self.market.available_dates(evaluation_end_date) if old.evaluation_end_date < day <= evaluation_end_date]
        if not new_dates:
            return {"performance_run_id": old.run_id, "added_evaluation_dates": 0, "duplicate_count": 0, "status": "NO_NEW_MARKET_DATA"}
        active = self.session.scalar(select(PipelineJob).where(
            PipelineJob.job_type == "SELECTION_PERFORMANCE_REFRESH", PipelineJob.status.in_(ACTIVE)
        ))
        if active:
            return {"job_id": active.job_id, "performance_run_id": old.run_id, "duplicate_status": "RUNNING", "status": "RUNNING"}
        job = PipelineJob(
            job_id=f"workbench-{uuid.uuid4().hex[:20]}", job_type="SELECTION_PERFORMANCE_REFRESH",
            trade_date=evaluation_end_date, status="PENDING", stage="VALIDATING_INPUT", progress_current=0,
            progress_total=max(1, old.cohort_count), checkpoint={"performance_run_id": old.run_id, "incremental": True,
            "old_evaluation_end_date": old.evaluation_end_date.isoformat(), "new_evaluation_end_date": evaluation_end_date.isoformat()},
        )
        old.status = "STALE"
        self.session.add(job)
        self.session.commit()
        return {"job_id": job.job_id, "performance_run_id": old.run_id, "added_evaluation_dates": len(new_dates), "duplicate_count": 0, "status": "PENDING"}

    def execute_incremental(self, run_id: str, job_id: str, evaluation_end_date: date) -> dict[str, Any]:
        run, job = self._run(run_id), self._job(job_id)
        old_end = run.evaluation_end_date
        snapshot = dict(run.config_snapshot_json["request"])
        snapshot["evaluation_end_date"] = evaluation_end_date.isoformat()
        request = _request_from_snapshot(snapshot)
        run.status, job.status, job.stage, job.started_at = "RUNNING", "RUNNING", "RESOLVING_COHORTS", datetime.now(timezone.utc)
        self.session.commit()
        cohorts = self.cohorts.resolve(request.start_selection_date, request.end_selection_date or request.evaluation_end_date, request.lookback_value)
        members_by_cohort = {cohort.id: self.cohorts.members(cohort, request.selection_scope, request.include_zero_position_stocks, request.include_risk_blocked_stocks) for cohort in cohorts}
        all_members = [member for members in members_by_cohort.values() for member in members]
        market_dates = self.market.available_dates(evaluation_end_date)
        bars, watermark = self.market.load({row.stock_code for row in all_members}, sorted(set(market_dates) | {row.selection_trade_date for row in cohorts}))
        added_stock: list[dict[str, Any]] = []
        added_portfolio: list[dict[str, Any]] = []
        for index, cohort in enumerate(cohorts, start=1):
            snapshots = [MemberSnapshot(row.id, row.stock_code, cohort.selection_trade_date, float(row.suggested_position_percent or 0)) for row in members_by_cohort[cohort.id]]
            evaluation_dates = [day for day in market_dates if cohort.selection_trade_date < day <= evaluation_end_date]
            all_stock = [row for member in snapshots for row in ReturnCalculator().calculate(member, bars.get(member.stock_code, {}), evaluation_dates, request.return_basis)]
            added_stock.extend(row for row in all_stock if row["evaluation_trade_date"] > old_end)
            all_portfolio = PortfolioReturnCalculator().calculate(cohort.id, snapshots, all_stock, request.weighting_mode)
            added_portfolio.extend(row for row in all_portfolio if row["evaluation_trade_date"] > old_end)
            job.progress_current = index
            self.session.commit()
        job.stage = "PERSISTING_RESULTS"
        self.session.add_all(SelectionPerformanceDaily(performance_run_id=run.id, **row) for row in added_stock)
        self.session.add_all(SelectionPortfolioDaily(performance_run_id=run.id, **row) for row in added_portfolio)
        run.evaluation_end_date = evaluation_end_date
        run.market_data_watermark_hash = watermark
        settings = run.config_snapshot_json["settings"]
        components, _ = self._input_components(request, cohorts, settings)
        run.performance_input_hash = stable_hash(components)
        run.config_snapshot_json = _json_safe({"request": request.snapshot(), "settings": settings, "input_components": components})
        run.daily_record_count = self.session.scalar(select(func.count()).select_from(SelectionPerformanceDaily).where(SelectionPerformanceDaily.performance_run_id == run.id)) or 0
        run.portfolio_record_count = self.session.scalar(select(func.count()).select_from(SelectionPortfolioDaily).where(SelectionPortfolioDaily.performance_run_id == run.id)) or 0
        valid = self.session.scalar(select(func.count()).select_from(SelectionPerformanceDaily).where(SelectionPerformanceDaily.performance_run_id == run.id, SelectionPerformanceDaily.daily_return.is_not(None))) or 0
        run.coverage_ratio = valid / run.daily_record_count if run.daily_record_count else 0
        run.status = "SUCCESS" if run.daily_record_count and float(run.coverage_ratio) >= float(settings["min_coverage_ratio"]) else "PARTIAL_SUCCESS"
        run.completed_at = datetime.now(timezone.utc)
        job.status, job.stage, job.finished_at = run.status, "COMPLETED", datetime.now(timezone.utc)
        job.success_count, job.failure_count = len(added_stock), sum(row["daily_return"] is None for row in added_stock)
        job.checkpoint = {**job.checkpoint, "added_stock_records": len(added_stock), "added_portfolio_records": len(added_portfolio), "duplicate_count": 0, "full_history_recalculated": False, "no_llm_call_verified": True, "per_stock_api_call_count": 0}
        self.session.commit()
        self._write_cache(run)
        return {"performance_run_id": run.run_id, "added_evaluation_dates": len({row["evaluation_trade_date"] for row in added_stock}), "added_stock_records": len(added_stock), "added_portfolio_records": len(added_portfolio), "duplicate_count": 0, "full_history_recalculated": False, "status": run.status}

    def invalidate(self, run_id: str, reason: str) -> dict[str, Any]:
        run = self._run(run_id)
        run.status, run.invalidation_reason = "INVALIDATED", reason.strip()[:500] or "MANUAL_INVALIDATION"
        self.session.commit()
        return self.run_detail(run_id)

    def cache_status(self, run_id: str | None = None) -> dict[str, Any]:
        run = self._run(run_id) if run_id else self.session.scalar(select(SelectionPerformanceRun).order_by(SelectionPerformanceRun.created_at.desc()))
        if run is None:
            return {"status": "NOT_RUN"}
        latest_dates = self.market.available_dates()
        latest = latest_dates[-1] if latest_dates else None
        status = run.status
        reason = run.invalidation_reason
        if status in {"SUCCESS", "PARTIAL_SUCCESS"} and latest and latest > run.evaluation_end_date:
            status, reason = "STALE", "NEW_MARKET_DATE_AVAILABLE"
        pipeline_ids = set(run.config_snapshot_json.get("input_components", {}).get("pipeline_run_ids", []))
        cohorts = [row for row in self.session.scalars(select(SelectionCohort)) if row.pipeline_run_id in pipeline_ids]
        members = [row for cohort in cohorts for row in self.cohorts.members(cohort, run.selection_scope, run.include_zero_position_stocks, run.include_risk_blocked_stocks)]
        dates = self.market.available_dates(run.evaluation_end_date)
        _, current_watermark = self.market.load({row.stock_code for row in members}, dates)
        if status in {"SUCCESS", "PARTIAL_SUCCESS"} and current_watermark != run.market_data_watermark_hash:
            status, reason = "INVALIDATED", "HISTORICAL_MARKET_DATA_CHANGED"
        if run.algorithm_version != ALGORITHM_VERSION or run.schema_version != SCHEMA_VERSION:
            status, reason = "INVALIDATED", "ALGORITHM_OR_SCHEMA_VERSION_CHANGED"
        if run.trade_calendar_version != TRADE_CALENDAR_VERSION:
            status, reason = "INVALIDATED", "TRADE_CALENDAR_VERSION_CHANGED"
        cache_path = self.cache.cache_dir / f"{run.run_id}.json"
        if run.cache_checksum and not self.cache.validate(cache_path, run.cache_checksum, int(run.cache_row_count or 0)):
            status, reason = "INVALIDATED", "CACHE_CHECKSUM_MISMATCH"
        return {"performance_run_id": run.run_id, "status": status, "cached_end_date": run.evaluation_end_date, "latest_market_date": latest, "reason": reason}

    def summary(self, run_id: str | None = None) -> dict[str, Any]:
        run = self._latest_queryable(run_id)
        if not run:
            return {"status": "NOT_RUN", "cohort_count": 0, "stock_count": 0}
        portfolios = self.session.scalars(select(SelectionPortfolioDaily).where(SelectionPortfolioDaily.performance_run_id == run.id)).all()
        latest_by_cohort = {}
        for row in portfolios:
            if row.cohort_id not in latest_by_cohort or row.evaluation_trade_date > latest_by_cohort[row.cohort_id].evaluation_trade_date:
                latest_by_cohort[row.cohort_id] = row
        cumulative = [float(row.cumulative_return) for row in latest_by_cohort.values() if row.cumulative_return is not None]
        daily = [float(row.daily_return) for row in portfolios if row.daily_return is not None]
        stocks = self.stock_daily(run.run_id)
        latest_by_stock = {}
        for row in stocks:
            key = (row["pipeline_run_id"], row["stock_code"])
            if key not in latest_by_stock or row["evaluation_trade_date"] > latest_by_stock[key]["evaluation_trade_date"]:
                latest_by_stock[key] = row
        stock_returns = [row["cumulative_return"] for row in latest_by_stock.values() if row["cumulative_return"] is not None]
        cohort_values = [(row.cohort_id, float(row.cumulative_return)) for row in latest_by_cohort.values() if row.cumulative_return is not None]
        cohort_map = {row.id: row for row in self.session.scalars(select(SelectionCohort))}
        best = max(cohort_values, key=lambda item: item[1], default=None)
        worst = min(cohort_values, key=lambda item: item[1], default=None)
        return {
            "performance_run_id": run.run_id, "status": run.status, "cohort_count": run.cohort_count,
            "stock_count": run.stock_count, "average_daily_return": sum(daily) / len(daily) if daily else None,
            "average_cumulative_return": sum(cumulative) / len(cumulative) if cumulative else None,
            "positive_portfolio_ratio": sum(value > 0 for value in cumulative) / len(cumulative) if cumulative else None,
            "positive_stock_ratio": sum(value > 0 for value in stock_returns) / len(stock_returns) if stock_returns else None,
            "best_selection_date": cohort_map[best[0]].selection_trade_date if best else None,
            "worst_selection_date": cohort_map[worst[0]].selection_trade_date if worst else None,
            "max_drawdown": min((float(row.max_drawdown_to_date) for row in portfolios if row.max_drawdown_to_date is not None), default=None),
            "coverage_ratio": float(run.coverage_ratio), "latest_evaluation_date": run.evaluation_end_date,
            "cache_status": self.cache_status(run.run_id)["status"],
            "no_llm_call_verified": True, "per_stock_api_call_count": 0,
        }

    def cohort_summary(self, run_id: str | None = None) -> list[dict[str, Any]]:
        run = self._latest_queryable(run_id)
        if not run:
            return []
        pipeline_ids = set(run.config_snapshot_json.get("input_components", {}).get("pipeline_run_ids", []))
        cohorts = [row for row in self.session.scalars(select(SelectionCohort).order_by(SelectionCohort.selection_trade_date.desc())) if row.pipeline_run_id in pipeline_ids]
        result = []
        for cohort in cohorts:
            rows = self.session.scalars(select(SelectionPortfolioDaily).where(SelectionPortfolioDaily.performance_run_id == run.id, SelectionPortfolioDaily.cohort_id == cohort.id).order_by(SelectionPortfolioDaily.evaluation_trade_date)).all()
            member_ids = set(self.session.scalars(select(SelectionCohortMember.id).where(SelectionCohortMember.cohort_id == cohort.id)))
            stock_rows = self.session.scalars(select(SelectionPerformanceDaily).where(SelectionPerformanceDaily.performance_run_id == run.id, SelectionPerformanceDaily.cohort_member_id.in_(member_ids)).order_by(SelectionPerformanceDaily.evaluation_trade_date)).all() if member_ids else []
            latest_stocks = {}
            for stock_row in stock_rows:
                latest_stocks[stock_row.cohort_member_id] = stock_row
            latest_returns = [float(row.cumulative_return) for row in latest_stocks.values() if row.cumulative_return is not None]
            result.append({
                "cohort_id": cohort.id, "selection_trade_date": cohort.selection_trade_date, "pipeline_run_id": cohort.pipeline_run_id,
                "candidate_count": cohort.stock_count, "llm_count": cohort.llm_count, "manual_count": cohort.manual_count,
                "both_count": cohort.both_count, "baseline_trade_date": rows[0].evaluation_trade_date if rows else None,
                "holding_days": len(rows), **{f"day_{index}_return": float(rows[index - 1].daily_return) if len(rows) >= index and rows[index - 1].daily_return is not None else None for index in range(1, 6)},
                "latest_daily_return": float(rows[-1].daily_return) if rows and rows[-1].daily_return is not None else None,
                "cumulative_return": float(rows[-1].cumulative_return) if rows and rows[-1].cumulative_return is not None else None,
                "positive_stock_count": sum(value > 0 for value in latest_returns),
                "negative_stock_count": sum(value < 0 for value in latest_returns),
                "win_rate": float(rows[-1].win_rate) if rows and rows[-1].win_rate is not None else None,
                "max_drawdown": float(rows[-1].max_drawdown_to_date) if rows and rows[-1].max_drawdown_to_date is not None else None,
                "coverage_ratio": float(rows[-1].coverage_ratio) if rows else 0.0, "cache_status": run.status, "run_status": run.status,
            })
        return result

    def portfolio_daily(self, run_id: str | None = None) -> list[dict[str, Any]]:
        run = self._latest_queryable(run_id)
        if not run:
            return []
        cohorts = {row.id: row for row in self.session.scalars(select(SelectionCohort))}
        rows = self.session.scalars(select(SelectionPortfolioDaily).where(SelectionPortfolioDaily.performance_run_id == run.id).order_by(SelectionPortfolioDaily.evaluation_trade_date)).all()
        return [{
            "selection_trade_date": cohorts[row.cohort_id].selection_trade_date, "pipeline_run_id": cohorts[row.cohort_id].pipeline_run_id,
            "evaluation_trade_date": row.evaluation_trade_date, "holding_day": row.holding_day, "weighting_mode": row.weighting_mode,
            "total_member_count": row.total_member_count, "valid_member_count": row.valid_member_count,
            "suspended_count": row.suspended_count, "missing_count": row.missing_count,
            "daily_return": _float(row.daily_return), "cumulative_return": _float(row.cumulative_return), "win_rate": _float(row.win_rate),
            "best_stock_code": row.best_stock_code, "worst_stock_code": row.worst_stock_code,
            "drawdown_to_date": _float(row.drawdown_to_date), "max_drawdown_to_date": _float(row.max_drawdown_to_date),
            "coverage_ratio": float(row.coverage_ratio), "status": row.status,
        } for row in rows]

    def stock_daily(self, run_id: str | None = None) -> list[dict[str, Any]]:
        run = self._latest_queryable(run_id)
        if not run:
            return []
        members = {row.id: row for row in self.session.scalars(select(SelectionCohortMember))}
        cohorts = {row.id: row for row in self.session.scalars(select(SelectionCohort))}
        rows = self.session.scalars(select(SelectionPerformanceDaily).where(SelectionPerformanceDaily.performance_run_id == run.id).order_by(SelectionPerformanceDaily.evaluation_trade_date)).all()
        return [{
            "selection_trade_date": cohorts[members[row.cohort_member_id].cohort_id].selection_trade_date,
            "pipeline_run_id": cohorts[members[row.cohort_member_id].cohort_id].pipeline_run_id,
            "stock_code": members[row.cohort_member_id].stock_code, "stock_name": members[row.cohort_member_id].stock_name_snapshot,
            "selection_source": members[row.cohort_member_id].selection_source, "quant_rank": members[row.cohort_member_id].quant_rank,
            "flash_rank": members[row.cohort_member_id].flash_rank, "pro_rank": members[row.cohort_member_id].pro_rank,
            "suggested_position_percent": _float(members[row.cohort_member_id].suggested_position_percent), "return_basis": run.return_basis,
            "baseline_trade_date": row.baseline_trade_date, "baseline_price": _float(row.baseline_price),
            "evaluation_trade_date": row.evaluation_trade_date, "holding_day": row.holding_day,
            "open_price": _float(row.open_price), "close_price": _float(row.close_price), "daily_return": _float(row.daily_return),
            "cumulative_return": _float(row.cumulative_return), "drawdown_to_date": _float(row.drawdown_to_date),
            "max_drawdown_to_date": _float(row.max_drawdown_to_date), "return_source": row.return_source, "data_status": row.data_status,
        } for row in rows]

    def runs(self) -> list[dict[str, Any]]:
        return [self.run_detail(row.run_id) for row in self.session.scalars(select(SelectionPerformanceRun).order_by(SelectionPerformanceRun.created_at.desc()))]

    def run_detail(self, run_id: str) -> dict[str, Any]:
        row = self._run(run_id)
        return {key: getattr(row, key) for key in (
            "run_id", "performance_input_hash", "evaluation_end_date", "lookback_value", "lookback_unit",
            "start_selection_date", "end_selection_date", "return_basis", "selection_scope", "weighting_mode",
            "status", "cohort_count", "stock_count", "daily_record_count", "portfolio_record_count", "coverage_ratio",
            "algorithm_version", "schema_version", "market_data_watermark_hash", "invalidation_reason", "error_message",
            "created_at", "completed_at",
        )}

    def methodology(self) -> dict[str, Any]:
        return {
            "name": "选股收益统计", "statement": "本统计反映选股后的市场价格表现，不代表实际成交收益或交易建议。",
            "next_open": "首日=收盘/次日开盘-1；后续优先使用已验证pct_chg。",
            "signal_close": "信号评价口径，不代表可以在选股日收盘价成交。",
            "cumulative": "product(1 + daily_return) - 1", "drawdown": "current_value / historical_peak_value - 1",
            "suspension": "确认停牌时延续最后有效估值并记录0收益；未知缺失不记0。",
            "database_source_of_truth": True, "no_llm_call_verified": True, "per_stock_api_call_count": 0,
        }

    def _input_components(self, request: PerformanceRequest, cohorts: list[SelectionCohort], settings: dict[str, Any]) -> tuple[dict[str, Any], str]:
        members = [member for cohort in cohorts for member in self.cohorts.members(cohort, request.selection_scope, request.include_zero_position_stocks, request.include_risk_blocked_stocks)]
        dates = self.market.available_dates(request.evaluation_end_date)
        _, watermark = self.market.load({row.stock_code for row in members}, dates)
        config_hash = stable_hash(settings)
        components = {
            "performance_algorithm_version": ALGORITHM_VERSION, "performance_schema_version": SCHEMA_VERSION,
            "start_selection_date": request.start_selection_date, "end_selection_date": request.end_selection_date,
            "evaluation_end_date": request.evaluation_end_date, "lookback_value": request.lookback_value,
            "lookback_unit": request.lookback_unit, "return_basis": request.return_basis,
            "selection_scope": request.selection_scope, "weighting_mode": request.weighting_mode,
            "include_zero_position_stocks": request.include_zero_position_stocks,
            "include_risk_blocked_stocks": request.include_risk_blocked_stocks,
            "pipeline_run_ids": [row.pipeline_run_id for row in cohorts],
            "candidate_set_hashes": [row.candidate_set_hash for row in cohorts],
            "quant_run_ids": [row.quant_run_id for row in cohorts], "position_run_ids": [row.position_run_id for row in cohorts],
            "market_data_watermark_hash": watermark, "trade_calendar_version": TRADE_CALENDAR_VERSION,
            "configuration_snapshot_hash": config_hash,
        }
        return components, watermark

    def _replace_results(self, run: SelectionPerformanceRun, stock_rows: list[dict[str, Any]], portfolio_rows: list[dict[str, Any]]) -> None:
        self.session.execute(delete(SelectionPerformanceDaily).where(SelectionPerformanceDaily.performance_run_id == run.id))
        self.session.execute(delete(SelectionPortfolioDaily).where(SelectionPortfolioDaily.performance_run_id == run.id))
        self.session.add_all(SelectionPerformanceDaily(performance_run_id=run.id, **row) for row in stock_rows)
        self.session.add_all(SelectionPortfolioDaily(performance_run_id=run.id, **row) for row in portfolio_rows)
        self.session.flush()

    def _write_cache(self, run: SelectionPerformanceRun) -> None:
        payload = {"cohorts": self.cohort_summary(run.run_id), "daily": self.portfolio_daily(run.run_id), "stocks": self.stock_daily(run.run_id)}
        checksum, row_count, _ = self.cache.write(run.run_id, {
            "schema_version": SCHEMA_VERSION, "algorithm_version": ALGORITHM_VERSION,
            "performance_input_hash": run.performance_input_hash,
            "market_data_watermark_hash": run.market_data_watermark_hash,
            "evaluation_end_date": run.evaluation_end_date, "status": run.status,
        }, payload)
        run.cache_checksum, run.cache_row_count = checksum, row_count
        self.session.commit()

    def _run_cohort_ids(self, run: SelectionPerformanceRun) -> set[int]:
        return set(self.session.scalars(select(SelectionPortfolioDaily.cohort_id).where(SelectionPortfolioDaily.performance_run_id == run.id)))

    def _latest_queryable(self, run_id: str | None) -> SelectionPerformanceRun | None:
        if run_id:
            return self._run(run_id)
        return self.session.scalar(select(SelectionPerformanceRun).where(SelectionPerformanceRun.status.in_(["SUCCESS", "PARTIAL_SUCCESS", "STALE"])).order_by(SelectionPerformanceRun.created_at.desc()))

    def _run(self, run_id: str | None) -> SelectionPerformanceRun:
        row = self.session.scalar(select(SelectionPerformanceRun).where(SelectionPerformanceRun.run_id == run_id))
        if row is None:
            raise ValueError("PERFORMANCE_RUN_NOT_FOUND")
        return row

    def _job(self, job_id: str) -> PipelineJob:
        row = self.session.scalar(select(PipelineJob).where(PipelineJob.job_id == job_id))
        if row is None:
            raise ValueError("PERFORMANCE_JOB_NOT_FOUND")
        return row


def _request_from_snapshot(value: dict[str, Any]) -> PerformanceRequest:
    return PerformanceRequest(**{
        **value,
        "evaluation_end_date": date.fromisoformat(value["evaluation_end_date"]),
        "start_selection_date": date.fromisoformat(value["start_selection_date"]) if value.get("start_selection_date") else None,
        "end_selection_date": date.fromisoformat(value["end_selection_date"]) if value.get("end_selection_date") else None,
    })


def _float(value) -> float | None:
    return float(value) if value is not None else None


def _json_safe(value):
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value
