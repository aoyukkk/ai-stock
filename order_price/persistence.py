from __future__ import annotations

from database.models.order_plan import OrderPlan, OrderPriceCandidate
from order_price.exceptions import OrderPricePersistenceError
from order_price.schemas import OrderPriceRanking


def persist_order_plans(session, ranking: OrderPriceRanking) -> None:
    try:
        for plan in ranking.plans:
            db_plan = OrderPlan(
                stock_code=plan.stock_code,
                plan_date=plan.plan_date,
                plan_session=plan.plan_session,
                side=plan.side,
                strategy_type=plan.strategy_type,
                recommended_price=plan.recommended_price,
                price_range_low=plan.price_range_low,
                price_range_high=plan.price_range_high,
                max_acceptable_price=plan.max_acceptable_price,
                stop_loss_price=plan.stop_loss_price,
                take_profit_1_price=plan.take_profit_1_price,
                take_profit_2_price=plan.take_profit_2_price,
                suggested_position_percent=plan.suggested_position_percent,
                confidence=plan.confidence,
                reason=plan.reason,
                valid_conditions=plan.valid_conditions,
                cancel_conditions=plan.cancel_conditions,
                reprice_conditions=plan.reprice_conditions,
                status=plan.status,
            )
            session.add(db_plan)
            session.flush()

            for candidate in plan.candidates:
                session.add(
                    OrderPriceCandidate(
                        order_plan_id=db_plan.id,
                        price_type=candidate.price_type,
                        price=candidate.price,
                        score=candidate.score,
                        fill_probability=candidate.fill_probability,
                        expected_return=candidate.expected_return,
                        risk_reward=candidate.risk_reward,
                        expected_profit_price=candidate.expected_profit_price,
                        stop_loss_price=candidate.stop_loss_price,
                        reason=candidate.reason,
                    )
                )
        session.commit()
    except Exception as exc:
        session.rollback()
        raise OrderPricePersistenceError("Failed to persist order plan drafts.") from exc
