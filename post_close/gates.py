from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from database.models import (
    OrderPlan,
    PositionTruthConfirmation,
    ProCandidateReview,
    ProResumeRun,
    TraderPositionSnapshot,
)
from database.models.workbench import ManualSelectionRecord
from stock_codes import normalize_ts_code


HELD_SCOPES = {"HUMAN_REFERENCE", "AI_SIMULATION"}
TERMINAL_ORDER_STATUSES = {"CANCELLED", "CANCELED", "EXPIRED", "FILLED", "INVALID", "REJECTED"}


class PositionTruthGate:
    def __init__(
        self,
        session,
        *,
        config: dict[str, Any] | None = None,
        ai_simulation_enabled: bool = False,
        requested_scopes: set[str] | None = None,
        now: datetime | None = None,
    ) -> None:
        self.session = session
        self.config = config or {
            "required_scopes": ["HUMAN_REFERENCE"],
            "require_ai_simulation_when_enabled": True,
            "maximum_snapshot_age_hours": 24,
        }
        self.ai_simulation_enabled = ai_simulation_enabled
        self.requested_scopes = set(requested_scopes or set())
        self.now = _aware_utc(now or datetime.now(timezone.utc))

    def evaluate(self, trade_date: date) -> dict[str, Any]:
        confirmations = {}
        positions = {}
        for scope in sorted(HELD_SCOPES):
            confirmation = self.session.scalar(select(PositionTruthConfirmation).where(
                PositionTruthConfirmation.account_scope == scope,
                PositionTruthConfirmation.is_current.is_(True),
            ).order_by(PositionTruthConfirmation.snapshot_time.desc()))
            confirmations[scope] = confirmation
            positions[scope] = list(self.session.scalars(select(TraderPositionSnapshot).where(
                TraderPositionSnapshot.account_scope == scope,
                TraderPositionSnapshot.is_current.is_(True),
            )))

        required_scopes = {
            scope for scope in self.config.get("required_scopes", ["HUMAN_REFERENCE"])
            if scope in HELD_SCOPES
        }
        required_scopes.add("HUMAN_REFERENCE")
        ai_has_positions = bool(positions["AI_SIMULATION"])
        if (
            "AI_SIMULATION" in self.requested_scopes
            or ai_has_positions
            or (
                bool(self.config.get("require_ai_simulation_when_enabled", True))
                and self.ai_simulation_enabled
            )
        ):
            required_scopes.add("AI_SIMULATION")

        missing_fields: list[str] = []
        required_statuses: list[str] = []
        scope_status: dict[str, str] = {}
        scope_details: dict[str, dict[str, Any]] = {}
        maximum_age_hours = max(1, int(self.config.get("maximum_snapshot_age_hours", 24)))
        for scope in sorted(HELD_SCOPES):
            confirmation = confirmations[scope]
            rows = positions[scope]
            required = scope in required_scopes
            invalid = [
                row.stock_code for row in rows
                if row.quantity < 0
                or row.available_quantity < 0
                or row.available_quantity > row.quantity
                or float(row.cost_price) <= 0
            ]
            status = "NOT_REQUIRED" if not required else "MISSING"
            stale = False
            if not required:
                pass
            if confirmation is None:
                if required:
                    missing_fields.append(f"{scope}:confirmation")
            elif required:
                age = self.now - _aware_utc(confirmation.snapshot_time)
                stale = (
                    confirmation.trade_date > trade_date
                    or confirmation.trade_date < trade_date - timedelta(days=1)
                    or age > timedelta(hours=maximum_age_hours)
                )
                if stale:
                    status = "STALE"
                elif confirmation.position_count != len(rows):
                    status = "CONFLICTED"
                elif invalid:
                    status = "INVALID"
                    missing_fields.extend(f"{scope}:{code}" for code in invalid)
                else:
                    status = confirmation.confirmation_status
            scope_status[scope] = status
            if required:
                required_statuses.append(status)
            scope_details[scope] = {
                "required": required,
                "status": status,
                "snapshot_time": confirmation.snapshot_time if confirmation else None,
                "position_count": len(rows),
                "confirmed_empty": status == "CONFIRMED_EMPTY",
                "stale": stale,
                "invalid_rows": invalid,
            }

        allowed = bool(required_statuses) and all(
            value in {"CONFIRMED_POSITIONS", "CONFIRMED_EMPTY"}
            for value in required_statuses
        )
        if allowed:
            status = "CONFIRMED_POSITIONS" if any(
                positions[scope] for scope in required_scopes
            ) else "CONFIRMED_EMPTY"
        else:
            priority = ("CONFLICTED", "INVALID", "STALE", "MISSING")
            status = next((value for value in priority if value in required_statuses), "MISSING")
        latest = max(
            (confirmations[scope].snapshot_time for scope in required_scopes if confirmations[scope]),
            default=None,
        )
        return {
            "status": status,
            "snapshot_time": latest,
            "required_scopes": sorted(required_scopes),
            "maximum_snapshot_age_hours": maximum_age_hours,
            "human_count": len(positions["HUMAN_REFERENCE"]),
            "ai_count": len(positions["AI_SIMULATION"]),
            "confirmed_empty": allowed and not any(positions[scope] for scope in required_scopes),
            "missing_fields": missing_fields,
            "action_run_allowed": allowed,
            "scope_status": scope_status,
            "scope_details": scope_details,
            "ai_simulation_required": "AI_SIMULATION" in required_scopes,
        }


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class PostCloseActionPoolResolver:
    def __init__(self, session) -> None:
        self.session = session

    def resolve(self, trade_date: date, *, mode: str) -> dict[str, Any]:
        final_run = self._final_run(trade_date, mode)
        source_date = final_run.base_trade_date if final_run else trade_date
        sources: dict[str, set[str]] = {
            "FINAL": set(), "MANUAL": set(), "HUMAN_HELD": set(),
            "AI_HELD": set(), "ACTIVE_ORDER_PLAN": set(),
        }
        if final_run:
            sources["FINAL"] = {
                normalize_ts_code(row.stock_code)
                for row in self.session.scalars(select(ProCandidateReview).where(
                    ProCandidateReview.pro_resume_run_id == final_run.run_id,
                ))
            }
        sources["MANUAL"] = {
            normalize_ts_code(row.stock_code)
            for row in self.session.scalars(select(ManualSelectionRecord).where(
                ManualSelectionRecord.trade_date == trade_date,
            ))
        }
        for row in self.session.scalars(select(TraderPositionSnapshot).where(TraderPositionSnapshot.is_current.is_(True))):
            origin = "HUMAN_HELD" if row.account_scope == "HUMAN_REFERENCE" else "AI_HELD"
            sources[origin].add(normalize_ts_code(row.stock_code))
        for row in self.session.scalars(select(OrderPlan).where(OrderPlan.plan_date.in_({trade_date, source_date}))):
            if str(row.status or "ACTIVE").upper() not in TERMINAL_ORDER_STATUSES:
                sources["ACTIVE_ORDER_PLAN"].add(normalize_ts_code(row.stock_code))

        memberships: dict[str, set[str]] = {}
        for origin, codes in sources.items():
            for code in codes:
                memberships.setdefault(code, set()).add(origin)
        items = [{"stock_code": code, "origins": sorted(origins)} for code, origins in sorted(memberships.items())]
        source_counts = {name: len(codes) for name, codes in sources.items()}
        raw_union_count = sum(source_counts.values())
        deduplicated_count = len(items)
        invariant = deduplicated_count <= raw_union_count
        pool_hash = hashlib.sha256(json.dumps(items, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return {
            "trade_date": trade_date,
            "source_trade_date": source_date,
            "pipeline_run_id": final_run.pipeline_run_id if final_run else None,
            "pool_version": "POST_CLOSE_FINAL_POOL_V1" if mode == "POST_CLOSE_FINAL" else "POST_CLOSE_FAST_POOL_V1",
            "items": items,
            "final_count": source_counts["FINAL"],
            "manual_count": source_counts["MANUAL"],
            "human_held_count": source_counts["HUMAN_HELD"],
            "ai_held_count": source_counts["AI_HELD"],
            "active_order_plan_count": source_counts["ACTIVE_ORDER_PLAN"],
            "raw_union_count": raw_union_count,
            "deduplicated_count": deduplicated_count,
            "duplicate_count": raw_union_count - deduplicated_count,
            "source_distribution": source_counts,
            "pool_hash": pool_hash,
            "invariant_status": "PASS" if invariant else "ACTION_POOL_COUNT_INVARIANT_VIOLATION",
        }

    def _final_run(self, trade_date: date, mode: str) -> ProResumeRun | None:
        query = select(ProResumeRun).where(ProResumeRun.status == "COMPLETED")
        if mode == "POST_CLOSE_FINAL":
            query = query.where(ProResumeRun.base_trade_date == trade_date)
        else:
            query = query.where(ProResumeRun.base_trade_date <= trade_date)
        return self.session.scalar(query.order_by(ProResumeRun.base_trade_date.desc(), ProResumeRun.created_at.desc()))
