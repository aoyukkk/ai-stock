from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


AlertType = Literal[
    "PRICE_RAPID_RISE",
    "PRICE_RAPID_DROP",
    "VOLUME_ABNORMAL",
    "TURNOVER_ABNORMAL",
    "NEWS_IMPORTANT",
    "AUCTION_ABNORMAL",
    "PENDING_ORDER_NEAR_FILL",
    "LIMIT_UP_DOWN",
    "BLACK_SWAN",
    "SYSTEM_RISK",
]
AlertSeverity = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
SuggestedAction = Literal["KEEP", "WATCH_ONLY", "CANCEL", "REPRICE", "BLOCK", "RISK_ALERT"]


class AlertModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AlertEvent(AlertModel):
    alert_id: str = Field(default_factory=lambda: str(uuid4()))
    stock_code: str
    stock_name: str | None = None
    alert_type: AlertType
    severity: AlertSeverity
    message: str
    trigger_value: Decimal | None = None
    threshold: Decimal | None = None
    source: str
    created_at: datetime
    related_order_plan_id: int | None = None
    related_virtual_order_id: int | None = None
    suggested_action: SuggestedAction
