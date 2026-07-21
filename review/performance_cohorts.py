from __future__ import annotations

import hashlib
import json
from datetime import date

from sqlalchemy import select

from backend.core.config_manager import ConfigManager
from database.models.performance import SelectionCohort, SelectionCohortMember
from database.models.validation import (
    ModelValidationAllocation,
    ModelValidationOrderPlan,
    ModelValidationRun,
    ModelValidationSample,
    ProCandidateReview,
    ProResumeRun,
)
from stock_codes import normalize_ts_code


class SelectionCohortResolver:
    def __init__(self, session) -> None:
        self.session = session

    def resolve(self, start_date: date | None, end_date: date, lookback_value: int) -> list[SelectionCohort]:
        runs = self.session.scalars(select(ModelValidationRun).where(
            ModelValidationRun.base_market_trade_date <= end_date,
            ModelValidationRun.status.in_(["SUCCESS", "PARTIAL_SUCCESS"]),
        ).order_by(ModelValidationRun.base_market_trade_date.desc(), ModelValidationRun.created_at.desc())).all()
        selected: dict[date, ModelValidationRun] = {}
        for run in runs:
            if start_date and run.base_market_trade_date < start_date:
                continue
            if run.base_market_trade_date in selected or not self._is_complete(run):
                continue
            selected[run.base_market_trade_date] = run
        dates = sorted(selected)[-lookback_value:]
        return [self._get_or_create(selected[day]) for day in dates]

    def members(self, cohort: SelectionCohort, scope: str, include_zero: bool, include_risk_blocked: bool) -> list[SelectionCohortMember]:
        rows = list(self.session.scalars(select(SelectionCohortMember).where(SelectionCohortMember.cohort_id == cohort.id)))
        if scope == "FINAL_CANDIDATES":
            minimum_score = self._minimum_recommendation_score()
            rows = [row for row in rows if row.pro_score is not None and float(row.pro_score) >= minimum_score]
        elif scope == "FINAL_LLM_ONLY":
            minimum_score = self._minimum_recommendation_score()
            rows = [
                row for row in rows
                if row.selection_source == "LLM"
                and row.pro_score is not None
                and float(row.pro_score) >= minimum_score
            ]
        elif scope == "LLM_ONLY":
            rows = [row for row in rows if row.selection_source == "LLM"]
        elif scope == "MANUAL_ONLY":
            rows = [row for row in rows if row.selection_source == "MANUAL"]
        elif scope == "BOTH_ONLY":
            rows = [row for row in rows if row.selection_source == "BOTH"]
        elif scope == "NON_ZERO_POSITION":
            rows = [row for row in rows if float(row.suggested_position_percent or 0) > 0]
        if not include_zero and scope != "ALL_CANDIDATES_INCLUDING_ZERO_POSITION":
            rows = [row for row in rows if float(row.suggested_position_percent or 0) > 0]
        if not include_risk_blocked:
            rows = [row for row in rows if row.risk_status not in {"BLOCKED", "REJECTED", "RISK_BLOCKED"}]
        return rows

    def _minimum_recommendation_score(self) -> float:
        return float(
            ConfigManager(session=self.session)
            .get_effective_config()["values"]
            .get("selection_performance.minimum_recommendation_score", 60)
        )

    def _is_complete(self, run: ModelValidationRun) -> bool:
        samples = self._candidate_samples(run.run_id)
        if not samples:
            return False
        codes = {normalize_ts_code(row.stock_code) for row in samples}
        allocations = {normalize_ts_code(row.stock_code) for row in self.session.scalars(select(ModelValidationAllocation).where(ModelValidationAllocation.validation_run_id == run.run_id))}
        plans = {normalize_ts_code(row.stock_code) for row in self.session.scalars(select(ModelValidationOrderPlan).where(ModelValidationOrderPlan.validation_run_id == run.run_id))}
        return codes <= allocations and codes <= plans

    def _get_or_create(self, run: ModelValidationRun) -> SelectionCohort:
        pro = self.session.scalar(select(ProResumeRun).where(
            ProResumeRun.flash_validation_run_id == run.run_id,
            ProResumeRun.status.in_(["COMPLETED", "PARTIAL_PRO_FAILURE"]),
        ).order_by(ProResumeRun.created_at.desc()))
        pipeline_run_id = pro.pipeline_run_id if pro else run.run_id
        existing = self.session.scalar(select(SelectionCohort).where(SelectionCohort.pipeline_run_id == pipeline_run_id))
        if existing:
            return existing
        samples = self._candidate_samples(run.run_id)
        reviews = {normalize_ts_code(row.stock_code): row for row in self.session.scalars(select(ProCandidateReview).where(ProCandidateReview.flash_validation_run_id == run.run_id))}
        allocations = {normalize_ts_code(row.stock_code): row for row in self.session.scalars(select(ModelValidationAllocation).where(ModelValidationAllocation.validation_run_id == run.run_id))}
        plans = {normalize_ts_code(row.stock_code): row for row in self.session.scalars(select(ModelValidationOrderPlan).where(ModelValidationOrderPlan.validation_run_id == run.run_id))}
        source_counts = {"LLM": 0, "MANUAL": 0, "BOTH": 0}
        member_payloads = []
        for sample in samples:
            code = normalize_ts_code(sample.stock_code)
            review = reviews.get(code)
            screening = sample.screening_result or {}
            meta = screening.get("_trader_demo") or {}
            source = _source(meta.get("selection_source"), bool(meta.get("llm_selected")), bool(meta.get("manual_selected")))
            source_counts[source] += 1
            allocation, plan = allocations.get(code), plans.get(code)
            member_payloads.append({
                "stock_code": code, "stock_name_snapshot": sample.stock_name, "selection_source": source,
                "quant_rank": sample.rank, "quant_score": (sample.quant_scores or {}).get("total_score"),
                "flash_rank": meta.get("llm_rank"), "flash_score": screening.get("llm_score"),
                "flash_decision": screening.get("screening_decision"), "pro_rank": review.pro_rank if review else None,
                "pro_score": review.pro_score if review else None,
                "suggested_position_percent": allocation.suggested_position_percent if allocation else 0,
                "risk_status": plan.status if plan else "UNKNOWN",
            })
        candidate_hash = _hash_members(member_payloads)
        position_ids = sorted({row.allocation_run_id for row in allocations.values()})
        cohort = SelectionCohort(
            selection_trade_date=run.base_market_trade_date, pipeline_run_id=pipeline_run_id,
            quant_run_id=run.quant_run_id, flash_run_id=run.run_id, pro_run_id=pro.run_id if pro else None,
            position_run_id=position_ids[0] if len(position_ids) == 1 else _hash_values(position_ids),
            candidate_set_hash=candidate_hash, stock_count=len(member_payloads), llm_count=source_counts["LLM"],
            manual_count=source_counts["MANUAL"], both_count=source_counts["BOTH"], status="IMMUTABLE",
            completed_at=run.updated_at, config_snapshot_json={
                "validation": run.config_snapshot,
                "candidate_set_hash": candidate_hash,
                "upstream_candidate_set_hash": pro.candidate_set_hash if pro else None,
                "cohort_policy": {
                    "version": "full-key-candidates-v1",
                    "contains_all_key_candidates": True,
                },
            },
        )
        self.session.add(cohort)
        self.session.flush()
        self.session.add_all(SelectionCohortMember(cohort_id=cohort.id, **payload) for payload in member_payloads)
        self.session.commit()
        return cohort

    def _candidate_samples(self, run_id: str) -> list[ModelValidationSample]:
        rows = self.session.scalars(select(ModelValidationSample).where(ModelValidationSample.validation_run_id == run_id)).all()
        return [row for row in rows if _candidate(row.screening_result or {})]


def _candidate(screening: dict) -> bool:
    meta = screening.get("_trader_demo") or {}
    return bool(meta.get("selection_source") or meta.get("trading_candidate") or meta.get("llm_selected") or meta.get("manual_selected"))


def _source(value, llm: bool, manual: bool) -> str:
    text = str(value or "").upper()
    if text == "BOTH" or (llm and manual):
        return "BOTH"
    if text.startswith("MANUAL") or manual:
        return "MANUAL"
    return "LLM"


def _hash_members(values: list[dict]) -> str:
    payload = [{"stock_code": item["stock_code"], "source": item["selection_source"]} for item in values]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _hash_values(values: list[str]) -> str | None:
    return hashlib.sha256("|".join(values).encode()).hexdigest() if values else None
