from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from database.models.order_plan import OrderReassessmentLog
from recheck.schemas import OrderRecheckResult


def persist_reassessment_log(session, result: OrderRecheckResult, trigger_type: str) -> None:
    session.add(
        OrderReassessmentLog(
            order_plan_id=result.order_plan_id,
            stock_code=result.stock_code,
            reassess_time=result.created_at,
            trigger_type=trigger_type,
            old_recommended_price=result.old_recommended_price,
            new_recommended_price=result.new_recommended_price,
            action=result.action,
            reason=result.reason,
        )
    )
    session.commit()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def reprice_from_latest(latest_price: Decimal, old_price: Decimal | None) -> Decimal | None:
    if old_price is None:
        return latest_price.quantize(Decimal("0.0001"))
    return min(old_price, latest_price).quantize(Decimal("0.0001"))
