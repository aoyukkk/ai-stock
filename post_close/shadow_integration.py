from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select

from database.models.validation import ModelValidationOrderPlan
from entry_timing.service_v2 import EntryTimingV2ShadowService
from quant.explainability.service import DecisionExplainabilityShadowService


class PostCloseShadowIntegrationService:
    """Run the post-close V2.2/V3 branch without write access to Baseline decisions.

    The shadow engines append only their own research tables. Baseline values are
    supplied as an immutable audit snapshot and verified again after the run.
    """

    def __init__(self, session) -> None:
        self.session = session

    def run(
        self,
        *,
        trade_date: date,
        quant_run_id: str,
        available_at_ts: datetime,
        baseline_snapshot: dict[str, Any],
        candidate_mode: str = "QUANT_TOP100",
    ) -> dict[str, Any]:
        baseline_hash_before = _hash(baseline_snapshot)
        order_count_before = self._order_count()
        v2 = EntryTimingV2ShadowService(self.session).run(
            trade_date,
            quant_run_id=quant_run_id,
            candidate_mode=candidate_mode,
            force_shadow=True,
        )
        v3 = DecisionExplainabilityShadowService(self.session).run(
            trade_date,
            source_v2_run_id=v2["run_id"],
            force_shadow=True,
            available_at_ts=available_at_ts,
        )
        baseline_hash_after = _hash(baseline_snapshot)
        order_count_after = self._order_count()
        if baseline_hash_before != baseline_hash_after:
            raise RuntimeError("SHADOW_MODIFIED_BASELINE_SNAPSHOT")
        if order_count_before != order_count_after:
            raise RuntimeError("SHADOW_CREATED_ORDER_PLAN")
        return {
            "status": "SUCCESS",
            "mode": "PARALLEL_READ_ONLY_SHADOW",
            "candidate_mode": candidate_mode,
            "v2": v2,
            "v3": v3,
            "baseline_hash_before": baseline_hash_before,
            "baseline_hash_after": baseline_hash_after,
            "baseline_unchanged": True,
            "order_plan_count_before": order_count_before,
            "order_plan_count_after": order_count_after,
            "orders_created": 0,
            "formal_permission": False,
        }

    def _order_count(self) -> int:
        return int(
            self.session.scalar(select(func.count()).select_from(ModelValidationOrderPlan)) or 0
        )


def _hash(value: Any) -> str:
    material = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
