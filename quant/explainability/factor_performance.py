from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from statistics import fmean
from typing import Any

from sqlalchemy import select

from database.models.decision_explainability import (
    AdmissionV3Run,
    FactorAttribution,
    FactorPerformanceHistory,
    StrategyTimingContract,
)
from database.models.performance import (
    SelectionCohort,
    SelectionCohortMember,
    SelectionPerformanceDaily,
    SelectionPerformanceRun,
)
from quant.explainability.factor_registry import FACTOR_FAMILIES
from quant.explainability.timing_contract import compute_forward_return, validate_timing_contract
from stock_codes import normalize_ts_code


FACTOR_PERFORMANCE_VERSION = "factor_performance_v2_shadow"


@dataclass(frozen=True)
class RealizedReturn:
    d1: float | None
    d3: float | None
    d5: float | None
    terminal_return: float | None
    max_drawdown: float | None


@dataclass(frozen=True)
class FactorRealizedObservation:
    factor_family: str
    rank_contribution: float
    outcome: RealizedReturn


@dataclass(frozen=True)
class FactorPerformanceResult:
    factor_family: str
    sample_count: int
    win_rate: float | None
    avg_return_d1: float | None
    avg_return_d3: float | None
    avg_return_d5: float | None
    avg_drawdown: float | None
    positive_contribution_rate: float | None
    negative_contribution_rate: float | None
    period: str


class FactorPerformanceCalculator:
    def aggregate(
        self,
        observations: list[FactorRealizedObservation],
        *,
        period: str,
    ) -> list[FactorPerformanceResult]:
        grouped = {family: [] for family in FACTOR_FAMILIES}
        for observation in observations:
            if (
                observation.factor_family in grouped
                and observation.outcome.terminal_return is not None
                and observation.rank_contribution != 0
            ):
                grouped[observation.factor_family].append(observation)
        output: list[FactorPerformanceResult] = []
        for family in FACTOR_FAMILIES:
            rows = grouped[family]
            terminal = [float(item.outcome.terminal_return) for item in rows]
            positive = sum(
                _same_direction(item.rank_contribution, float(item.outcome.terminal_return))
                for item in rows
            )
            negative = sum(
                _opposite_direction(item.rank_contribution, float(item.outcome.terminal_return))
                for item in rows
            )
            output.append(FactorPerformanceResult(
                factor_family=family,
                sample_count=len(rows),
                win_rate=_ratio(sum(value > 0 for value in terminal), len(terminal)),
                avg_return_d1=_average(item.outcome.d1 for item in rows),
                avg_return_d3=_average(item.outcome.d3 for item in rows),
                avg_return_d5=_average(item.outcome.d5 for item in rows),
                avg_drawdown=_average(item.outcome.max_drawdown for item in rows),
                positive_contribution_rate=_ratio(positive, len(rows)),
                negative_contribution_rate=_ratio(negative, len(rows)),
                period=period,
            ))
        return output


class RealizedReturnRepository:
    """Resolve one latest successful return snapshot per selection/stock/holding day."""

    def __init__(self, session) -> None:
        self.session = session

    def load(self, period_start: date, period_end: date) -> dict[tuple[date, str], RealizedReturn]:
        rows = self.session.execute(
            select(
                SelectionPerformanceDaily,
                SelectionPerformanceRun,
                SelectionCohortMember,
                SelectionCohort,
            )
            .join(
                SelectionPerformanceRun,
                SelectionPerformanceDaily.performance_run_id == SelectionPerformanceRun.id,
            )
            .join(
                SelectionCohortMember,
                SelectionPerformanceDaily.cohort_member_id == SelectionCohortMember.id,
            )
            .join(SelectionCohort, SelectionCohortMember.cohort_id == SelectionCohort.id)
            .where(
                SelectionPerformanceRun.status == "SUCCESS",
                SelectionCohort.selection_trade_date >= period_start,
                SelectionCohort.selection_trade_date <= period_end,
                SelectionPerformanceDaily.holding_day <= 5,
                SelectionPerformanceDaily.cumulative_return.is_not(None),
            )
        ).all()
        latest: dict[tuple[date, str, int], tuple[tuple[Any, ...], SelectionPerformanceDaily]] = {}
        for daily, run, member, cohort in rows:
            key = (cohort.selection_trade_date, normalize_ts_code(member.stock_code), daily.holding_day)
            rank = (run.evaluation_end_date, run.created_at, run.id, daily.created_at, daily.id)
            if key not in latest or rank > latest[key][0]:
                latest[key] = (rank, daily)
        grouped: dict[tuple[date, str], dict[int, SelectionPerformanceDaily]] = {}
        for (selection_date, stock_code, holding_day), (_, daily) in latest.items():
            grouped.setdefault((selection_date, stock_code), {})[holding_day] = daily
        output: dict[tuple[date, str], RealizedReturn] = {}
        for key, by_day in grouped.items():
            terminal_day = max(by_day)
            drawdowns = [
                float(row.max_drawdown_to_date)
                for row in by_day.values()
                if row.max_drawdown_to_date is not None
            ]
            output[key] = RealizedReturn(
                d1=_daily_return(by_day, 1),
                d3=_daily_return(by_day, 3),
                d5=_daily_return(by_day, 5),
                terminal_return=_daily_return(by_day, terminal_day),
                max_drawdown=min(drawdowns) if drawdowns else None,
            )
        return output


class FactorPerformanceService:
    def __init__(self, session) -> None:
        self.session = session
        self.returns = RealizedReturnRepository(session)
        self.calculator = FactorPerformanceCalculator()

    def evaluate(self, period_start: date, period_end: date, *, persist: bool = True) -> list[dict[str, Any]]:
        if period_start > period_end:
            raise ValueError("INVALID_FACTOR_PERFORMANCE_PERIOD")
        runs = _latest_shadow_runs(self.session, period_start, period_end)
        outcomes = self.returns.load(period_start, period_end)
        observations: list[FactorRealizedObservation] = []
        input_rows: list[list[Any]] = []
        if runs:
            factors = list(self.session.scalars(
                select(FactorAttribution)
                .where(FactorAttribution.run_id.in_([row.run_id for row in runs]))
                .order_by(FactorAttribution.trade_date, FactorAttribution.stock_code, FactorAttribution.factor_family)
            ))
            contracts = {
                row.id: row
                for row in self.session.scalars(
                    select(StrategyTimingContract).where(
                        StrategyTimingContract.id.in_({item.timing_contract_id for item in factors})
                    )
                )
            }
            for factor in factors:
                outcome = outcomes.get((factor.trade_date, normalize_ts_code(factor.stock_code)))
                if outcome is None or outcome.terminal_return is None:
                    continue
                contract = contracts.get(factor.timing_contract_id)
                if contract is None:
                    raise ValueError("INVALID_TIMING_CONTRACT")
                validate_timing_contract(contract)
                guarded = compute_forward_return(contract, lambda value=outcome: value)
                observations.append(FactorRealizedObservation(
                    factor_family=factor.factor_family,
                    rank_contribution=float(factor.rank_contribution),
                    outcome=guarded,
                ))
                input_rows.append([
                    factor.trade_date,
                    factor.stock_code,
                    factor.factor_family,
                    str(factor.rank_contribution),
                    guarded.d1,
                    guarded.d3,
                    guarded.d5,
                    guarded.terminal_return,
                    guarded.max_drawdown,
                ])
        period = f"{period_start.isoformat()}..{period_end.isoformat()}"
        results = self.calculator.aggregate(observations, period=period)
        input_hash = _hash({
            "period": period,
            "version": FACTOR_PERFORMANCE_VERSION,
            "runs": [row.run_id for row in runs],
            "rows": input_rows,
        })
        if persist:
            existing = list(self.session.scalars(
                select(FactorPerformanceHistory).where(FactorPerformanceHistory.input_hash == input_hash)
            ))
            if not existing:
                self.session.add_all(FactorPerformanceHistory(
                    factor_family=row.factor_family,
                    sample_count=row.sample_count,
                    win_rate=row.win_rate,
                    avg_return_d1=row.avg_return_d1,
                    avg_return_d3=row.avg_return_d3,
                    avg_return_d5=row.avg_return_d5,
                    avg_drawdown=row.avg_drawdown,
                    positive_contribution_rate=row.positive_contribution_rate,
                    negative_contribution_rate=row.negative_contribution_rate,
                    period=row.period,
                    period_start=period_start,
                    period_end=period_end,
                    input_hash=input_hash,
                    version=FACTOR_PERFORMANCE_VERSION,
                ) for row in results)
                self.session.commit()
        return [_result_payload(row) for row in results]

    def latest(self, end_date: date) -> list[dict[str, Any]]:
        latest = self.session.scalar(
            select(FactorPerformanceHistory)
            .where(FactorPerformanceHistory.period_end <= end_date)
            .order_by(FactorPerformanceHistory.created_at.desc())
        )
        if latest is None:
            return []
        rows = list(self.session.scalars(
            select(FactorPerformanceHistory)
            .where(FactorPerformanceHistory.input_hash == latest.input_hash)
            .order_by(FactorPerformanceHistory.factor_family)
        ))
        return [{
            "factor_family": row.factor_family,
            "sample_count": row.sample_count,
            "win_rate": _optional_float(row.win_rate),
            "avg_return_d1": _optional_float(row.avg_return_d1),
            "avg_return_d3": _optional_float(row.avg_return_d3),
            "avg_return_d5": _optional_float(row.avg_return_d5),
            "avg_drawdown": _optional_float(row.avg_drawdown),
            "positive_contribution_rate": _optional_float(row.positive_contribution_rate),
            "negative_contribution_rate": _optional_float(row.negative_contribution_rate),
            "period": row.period,
            "version": row.version,
        } for row in rows]


def _latest_shadow_runs(session, period_start: date, period_end: date) -> list[AdmissionV3Run]:
    rows = list(session.scalars(
        select(AdmissionV3Run)
        .where(
            AdmissionV3Run.trade_date >= period_start,
            AdmissionV3Run.trade_date <= period_end,
            AdmissionV3Run.status == "SUCCESS",
            AdmissionV3Run.shadow_only.is_(True),
        )
        .order_by(AdmissionV3Run.trade_date, AdmissionV3Run.created_at)
    ))
    latest: dict[date, AdmissionV3Run] = {}
    for row in rows:
        latest[row.trade_date] = row
    return [latest[key] for key in sorted(latest)]


def _daily_return(rows: dict[int, SelectionPerformanceDaily], holding_day: int) -> float | None:
    row = rows.get(holding_day)
    return float(row.cumulative_return) if row is not None and row.cumulative_return is not None else None


def _same_direction(contribution: float, realized_return: float) -> bool:
    return contribution != 0 and realized_return != 0 and contribution * realized_return > 0


def _opposite_direction(contribution: float, realized_return: float) -> bool:
    return contribution != 0 and realized_return != 0 and contribution * realized_return < 0


def _average(values) -> float | None:
    available = [float(value) for value in values if value is not None]
    return round(fmean(available), 6) if available else None


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _optional_float(value: Any) -> float | None:
    return float(value) if value is not None else None


def _result_payload(row: FactorPerformanceResult) -> dict[str, Any]:
    return {
        "factor_family": row.factor_family,
        "sample_count": row.sample_count,
        "win_rate": row.win_rate,
        "avg_return_d1": row.avg_return_d1,
        "avg_return_d3": row.avg_return_d3,
        "avg_return_d5": row.avg_return_d5,
        "avg_drawdown": row.avg_drawdown,
        "positive_contribution_rate": row.positive_contribution_rate,
        "negative_contribution_rate": row.negative_contribution_rate,
        "period": row.period,
        "version": FACTOR_PERFORMANCE_VERSION,
    }


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()
