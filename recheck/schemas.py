from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from alerts.schemas import AlertEvent


RecheckAction = Literal["KEEP", "CANCEL", "REPRICE", "BLOCK", "WATCH_ONLY"]


class RecheckModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class OrderRecheckInput(RecheckModel):
    order_plan_id: int
    stock_code: str
    old_recommended_price: Decimal | None
    latest_price: Decimal
    auction_price: Decimal
    auction_change_percent: Decimal
    limit_up_price: Decimal
    limit_down_price: Decimal
    risk_level: str
    news_importance: Decimal
    trigger_type: str


class OrderRecheckResult(RecheckModel):
    order_plan_id: int
    stock_code: str
    action: RecheckAction
    old_recommended_price: Decimal | None
    new_recommended_price: Decimal | None
    reason: str
    risk_level: str
    alert_events: list[AlertEvent]
    created_at: datetime
