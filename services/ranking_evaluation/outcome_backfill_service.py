from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from database.models.ranking_evaluation import (
    RankingEvaluationForwardOutcome,
    RankingEvaluationSnapshot,
    RankingEvaluationSnapshotItem,
)
from services.ranking_evaluation.constants import DEFAULT_HORIZONS, RETURN_BASIS, load_config
from services.ranking_evaluation.data_quality_service import RankingDataQualityService
from services.ranking_evaluation.market_data_service import RankingMarketDataService
from services.ranking_evaluation.metric_calculator import RankingMetricCalculator
from services.ranking_evaluation.trading_calendar_service import RankingTradingCalendarService
from services.ranking_evaluation.utils import stable_hash


TERMINAL = {"MATURED", "CORPORATE_ACTION_REVIEW"}


class OutcomeBackfillService:
    def __init__(
        self,
        session,
        *,
        calendar: RankingTradingCalendarService | None = None,
        market: RankingMarketDataService | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.session = session
        self.config = config or load_config()
        self.calendar = calendar or RankingTradingCalendarService()
        self.market = market or RankingMarketDataService(session)
        self.quality = RankingDataQualityService(session)

    def refresh(
        self,
        *,
        as_of_date: date,
        factor_version: str | None = None,
    ) -> dict[str, Any]:
        statement = select(RankingEvaluationSnapshot).where(
            RankingEvaluationSnapshot.ranking_trade_date < as_of_date
        )
        if factor_version:
            statement = statement.where(
                RankingEvaluationSnapshot.factor_version == factor_version
            )
        snapshots = list(
            self.session.scalars(
                statement.order_by(
                    RankingEvaluationSnapshot.ranking_trade_date,
                    RankingEvaluationSnapshot.factor_version,
                )
            )
        )
        counters = {
            "created": 0,
            "matured": 0,
            "not_matured": 0,
            "missing": 0,
            "conflicts": 0,
            "idempotent": 0,
        }
        touched_snapshots: set[int] = set()
        now = datetime.now(timezone.utc)
        for snapshot in snapshots:
            try:
                due_dates = self.calendar.horizon_dates(
                    snapshot.ranking_trade_date, DEFAULT_HORIZONS
                )
            except ValueError as exc:
                self.quality.record(
                    snapshot_id=snapshot.id,
                    issue_code="TRADE_CALENDAR_HORIZON_INCOMPLETE",
                    issue_level="ABNORMAL",
                    affected_date=snapshot.ranking_trade_date,
                    affected_version=snapshot.factor_version,
                    detail=str(exc),
                )
                counters["missing"] += 1
                continue
            items = list(
                self.session.scalars(
                    select(RankingEvaluationSnapshotItem)
                    .where(RankingEvaluationSnapshotItem.snapshot_id == snapshot.id)
                    .order_by(RankingEvaluationSnapshotItem.source_row_number)
                )
            )
            for item in items:
                for horizon, due_date in due_dates.items():
                    payload = self._evaluate(
                        item=item,
                        due_date=due_date,
                        horizon=horizon,
                        as_of_date=as_of_date,
                        now=now,
                    )
                    outcome = self.session.scalar(
                        select(RankingEvaluationForwardOutcome).where(
                            RankingEvaluationForwardOutcome.snapshot_item_id == item.id,
                            RankingEvaluationForwardOutcome.horizon == horizon,
                        )
                    )
                    action = self._persist(outcome, item, payload)
                    counters[action] += 1
                    if action in {"created", "matured", "missing", "conflicts"}:
                        touched_snapshots.add(snapshot.id)
        self.session.commit()
        metric_count = 0
        calculator = RankingMetricCalculator(self.session, config=self.config)
        for snapshot_id in sorted(touched_snapshots):
            metric_count += calculator.rebuild_snapshot(snapshot_id)
        self.session.commit()
        return {
            "status": "COMPLETED",
            "as_of_date": as_of_date,
            "factor_version": factor_version,
            "snapshot_count": len(snapshots),
            "metrics_rebuilt": metric_count,
            **counters,
            "external_api_calls": 0,
            "llm_calls": 0,
            "orders": 0,
            "scheduler": False,
            "return_basis": RETURN_BASIS,
        }

    def _evaluate(self, *, item, due_date, horizon, as_of_date, now):
        base = float(item.baseline_close) if item.baseline_close is not None else None
        common = {
            "horizon": horizon,
            "due_trade_date": due_date,
            "baseline_close": base,
            "baseline_price_source": item.baseline_price_source,
            "future_close": None,
            "return_decimal": None,
            "return_percent": None,
            "adjusted_return_decimal": None,
            "adjusted_return_percent": None,
            "baseline_adjustment_factor": None,
            "future_adjustment_factor": None,
            "future_price_source": None,
            "source_data_hash": None,
            "missing_reason": None,
            "corporate_action_flag": "UNKNOWN",
            "calculated_at": now,
            "data_as_of_time": now,
        }
        if due_date > as_of_date:
            return {**common, "outcome_status": "NOT_MATURED"}
        if base is None or base <= 0:
            return {
                **common,
                "outcome_status": "BASELINE_CLOSE_MISSING",
                "missing_reason": "Frozen snapshot has no valid baseline close.",
            }
        batch = self.market.load_day(due_date)
        if batch.status == "PIPELINE_ERROR":
            return {
                **common,
                "outcome_status": "PIPELINE_ERROR",
                "source_data_hash": batch.source_hash,
                "missing_reason": "Due-date daily cache cannot be parsed.",
            }
        if item.ts_code in batch.duplicate_codes:
            return {
                **common,
                "outcome_status": "DUPLICATE_SOURCE_DATA",
                "source_data_hash": batch.source_hash,
                "missing_reason": "Due-date daily cache has duplicate stock rows.",
            }
        if item.ts_code in batch.invalid_date_rows:
            return {
                **common,
                "outcome_status": "WRONG_TRADE_DATE",
                "source_data_hash": batch.source_hash,
                "missing_reason": "Daily row trade_date differs from due_trade_date.",
            }
        price = batch.rows.get(item.ts_code)
        if price is None:
            stock_status = self.market.stock_status(item.ts_code)
            if stock_status == "DELISTED":
                status = "DELISTED"
            elif stock_status == "SUSPENDED":
                status = "SUSPENDED_ON_DUE_DATE"
            else:
                status = "FUTURE_CLOSE_MISSING"
            return {
                **common,
                "outcome_status": status,
                "source_data_hash": batch.source_hash,
                "missing_reason": f"No due-date close; stock master status={stock_status}.",
            }
        if price.suspended:
            return {
                **common,
                "outcome_status": "SUSPENDED_ON_DUE_DATE",
                "future_price_source": price.source,
                "source_data_hash": price.source_hash,
                "missing_reason": "Suspension is retained on the original due date; horizon is not shifted.",
            }
        if price.trade_date != due_date:
            return {
                **common,
                "outcome_status": "WRONG_TRADE_DATE",
                "future_price_source": price.source,
                "source_data_hash": price.source_hash,
                "missing_reason": "Loaded daily row date differs from due date.",
            }
        if price.close is None or price.close <= 0:
            return {
                **common,
                "outcome_status": "FUTURE_CLOSE_MISSING",
                "future_price_source": price.source,
                "source_data_hash": price.source_hash,
                "missing_reason": "Due-date close is null or non-positive.",
            }
        raw_return = price.close / base - 1.0
        baseline_adj, baseline_adj_hash = self.market.adjustment_factor(
            item.baseline_trade_date, item.ts_code
        )
        future_adj, future_adj_hash = self.market.adjustment_factor(
            due_date, item.ts_code
        )
        adjusted = None
        flag = "UNKNOWN"
        status = "MATURED"
        if baseline_adj and future_adj and baseline_adj > 0:
            adjusted = (price.close * future_adj) / (base * baseline_adj) - 1.0
            flag = "DETECTED" if abs(future_adj - baseline_adj) > 1e-12 else "NONE"
            if flag == "DETECTED":
                status = "CORPORATE_ACTION_REVIEW"
        source_hash = stable_hash(
            {
                "baseline": item.baseline_source_hash,
                "future": price.source_hash,
                "baseline_adj": baseline_adj_hash,
                "future_adj": future_adj_hash,
                "return_basis": RETURN_BASIS,
            }
        )
        return {
            **common,
            "future_close": price.close,
            "return_decimal": raw_return,
            "return_percent": raw_return * 100,
            "adjusted_return_decimal": adjusted,
            "adjusted_return_percent": adjusted * 100 if adjusted is not None else None,
            "baseline_adjustment_factor": baseline_adj,
            "future_adjustment_factor": future_adj,
            "future_price_source": price.source,
            "source_data_hash": source_hash,
            "outcome_status": status,
            "corporate_action_flag": flag,
        }

    def _persist(self, outcome, item, payload):
        if outcome is None:
            self.session.add(
                RankingEvaluationForwardOutcome(snapshot_item_id=item.id, **payload)
            )
            return "created"
        if outcome.outcome_status in TERMINAL:
            if (
                outcome.source_data_hash == payload.get("source_data_hash")
                and outcome.outcome_status == payload["outcome_status"]
            ):
                return "idempotent"
            if payload["outcome_status"] == "NOT_MATURED":
                return "idempotent"
            self.quality.record(
                snapshot_id=item.snapshot_id,
                issue_code="MATURED_OUTCOME_SOURCE_CHANGED",
                issue_level="ABNORMAL",
                affected_date=payload["due_trade_date"],
                affected_stock=item.stock_code,
                detail="A matured outcome source/hash changed; frozen result was not overwritten.",
            )
            return "conflicts"
        previous_status = outcome.outcome_status
        for key, value in payload.items():
            setattr(outcome, key, _decimal(value) if key in _DECIMAL_FIELDS else value)
        if payload["outcome_status"] in TERMINAL:
            return "matured"
        if payload["outcome_status"] == "NOT_MATURED":
            return "not_matured"
        if previous_status == payload["outcome_status"]:
            return "idempotent"
        return "missing"


_DECIMAL_FIELDS = {
    "baseline_close",
    "future_close",
    "return_decimal",
    "return_percent",
    "adjusted_return_decimal",
    "adjusted_return_percent",
    "baseline_adjustment_factor",
    "future_adjustment_factor",
}


def _decimal(value):
    return Decimal(str(value)) if value is not None else None
