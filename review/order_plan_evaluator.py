from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from database.models.order_plan import OrderPlan
from database.session import get_session, init_db
from datasource.schemas import KlineBar
from datasource.service import DataSourceService
from review.config import ReviewConfig, load_review_config
from review.persistence import persist_order_plan_evaluation
from review.schemas import OrderPlanEvaluationResult


class OrderPlanEvaluator:
    def __init__(
        self,
        session=None,
        data_source_service: DataSourceService | None = None,
        config: ReviewConfig | None = None,
    ) -> None:
        self.session = session
        self.data_source_service = data_source_service or DataSourceService()
        self.config = config or load_review_config()

    def evaluate_order_plan(self, order_plan_id: int, evaluation_date: date) -> OrderPlanEvaluationResult:
        session, own_session = self._session()
        try:
            plan = session.get(OrderPlan, order_plan_id)
            if plan is None:
                return OrderPlanEvaluationResult(
                    order_plan_id=order_plan_id,
                    stock_code="",
                    evaluation_date=evaluation_date,
                    evaluation_reason="Order plan not found.",
                )

            bars = self.data_source_service.get_kline(plan.stock_code, evaluation_date, evaluation_date, "1d")
            if not bars:
                result = OrderPlanEvaluationResult(
                    order_plan_id=plan.id,
                    stock_code=plan.stock_code,
                    evaluation_date=evaluation_date,
                    recommended_price=plan.recommended_price,
                    evaluation_reason="No mock market data available for evaluation date.",
                )
                persist_order_plan_evaluation(session, result)
                return result

            result = self._evaluate_with_bar(plan, bars[-1], evaluation_date)
            persist_order_plan_evaluation(session, result, actual_volume=bars[-1].volume)
            return result
        finally:
            if own_session:
                session.close()

    def evaluate_order_plans_for_date(self, evaluation_date: date) -> list[OrderPlanEvaluationResult]:
        session, own_session = self._session()
        try:
            plans = session.scalars(
                select(OrderPlan)
                .where(OrderPlan.plan_date <= evaluation_date)
                .order_by(OrderPlan.id)
            ).all()
            return [self.evaluate_order_plan(plan.id, evaluation_date) for plan in plans]
        finally:
            if own_session:
                session.close()

    def _evaluate_with_bar(self, plan: OrderPlan, bar: KlineBar, evaluation_date: date) -> OrderPlanEvaluationResult:
        price = plan.recommended_price
        side = (plan.side or "BUY").upper()
        if price is None:
            return OrderPlanEvaluationResult(
                order_plan_id=plan.id,
                stock_code=plan.stock_code,
                evaluation_date=evaluation_date,
                recommended_price=None,
                actual_open=bar.open,
                actual_high=bar.high,
                actual_low=bar.low,
                actual_close=bar.close,
                evaluation_reason="Order plan has no recommended price.",
            )

        was_filled = bar.low <= price <= bar.high
        simulated_fill_price = price if was_filled else None
        best_possible_price = bar.low if side == "BUY" else bar.high
        worst_possible_price = bar.high if side == "BUY" else bar.low
        missed_opportunity = self._missed_opportunity(side, price, bar, was_filled)
        risk_avoided = self._risk_avoided(side, price, bar, was_filled)
        quality_score, reason = self._quality_score(plan, bar, was_filled, missed_opportunity, risk_avoided)

        return OrderPlanEvaluationResult(
            order_plan_id=plan.id,
            stock_code=plan.stock_code,
            evaluation_date=evaluation_date,
            recommended_price=price,
            actual_open=bar.open,
            actual_high=bar.high,
            actual_low=bar.low,
            actual_close=bar.close,
            was_filled=was_filled,
            simulated_fill_price=simulated_fill_price,
            best_possible_price=best_possible_price,
            worst_possible_price=worst_possible_price,
            price_quality_score=quality_score,
            missed_opportunity=missed_opportunity,
            risk_avoided=risk_avoided,
            evaluation_reason=reason,
        )

    def _quality_score(
        self,
        plan: OrderPlan,
        bar: KlineBar,
        was_filled: bool,
        missed_opportunity: bool,
        risk_avoided: bool,
    ) -> tuple[Decimal, str]:
        price = plan.recommended_price or Decimal("0")
        side = (plan.side or "BUY").upper()
        quality = self.config.order_price_quality
        score = quality["filled_score"] if was_filled else quality["missed_but_good_price_score"]
        reasons: list[str] = ["Filled inside daily range." if was_filled else "Not filled by mock daily range."]

        if side == "BUY" and plan.max_acceptable_price is not None and price > plan.max_acceptable_price:
            score -= quality["too_aggressive_penalty"]
            reasons.append("Recommended BUY price exceeded max acceptable price.")
        elif side == "BUY" and price > bar.high:
            score -= quality["too_aggressive_penalty"]
            reasons.append("Recommended BUY price was above daily high.")
        elif side == "SELL" and price < bar.low:
            score -= quality["too_aggressive_penalty"]
            reasons.append("Recommended SELL price was below daily low.")

        if missed_opportunity:
            score -= quality["too_conservative_penalty"]
            reasons.append("Conservative price missed a favorable move.")

        if risk_avoided:
            score += quality["risk_avoided_bonus"]
            reasons.append("Unfilled plan avoided a mock intraday downside risk.")

        return _clamp_score(score), " ".join(reasons)

    def _missed_opportunity(self, side: str, price: Decimal, bar: KlineBar, was_filled: bool) -> bool:
        if was_filled:
            return False
        if side == "BUY":
            return price < bar.low and bar.close > bar.open * Decimal("1.02")
        return price > bar.high and bar.close < bar.open * Decimal("0.98")

    def _risk_avoided(self, side: str, price: Decimal, bar: KlineBar, was_filled: bool) -> bool:
        if was_filled or side != "BUY":
            return False
        return price < bar.low and bar.close < bar.open * Decimal("0.97")

    def _session(self):
        if self.session is not None:
            return self.session, False
        init_db()
        return get_session(), True


def _clamp_score(value: Decimal) -> Decimal:
    return max(Decimal("0"), min(Decimal("100"), value)).quantize(Decimal("0.0001"))
