from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models.allocation import AllocationRun, PositionSuggestionRecord
from position_sizing.schemas import AllocationResult


class AllocationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def save(self, run_payload: dict, result: AllocationResult) -> AllocationRun:
        request_hash = run_payload.get("request_hash")
        if request_hash:
            existing = self.session.scalar(select(AllocationRun).where(AllocationRun.request_hash == request_hash))
            if existing:
                return existing
        row = AllocationRun(**run_payload)
        self.session.add(row)
        self.session.flush()
        for item in result.suggestions:
            self.session.add(PositionSuggestionRecord(
                run_id=row.run_id,
                stock_code=item.stock_code,
                status=item.status.value,
                relative_allocation_weight=item.relative_allocation_weight,
                account_position_percent=item.account_position_percent,
                suggested_capital=item.suggested_capital,
                suggested_quantity=item.suggested_quantity,
                risk_per_share=item.risk_per_share,
                maximum_planned_loss=item.maximum_planned_loss,
                binding_constraint=item.binding_constraint,
                constraint_quantities=item.constraint_quantities,
                warnings=item.warnings,
            ))
        self.session.commit()
        self.session.refresh(row)
        return row

    def latest(self) -> AllocationRun | None:
        return self.session.scalar(select(AllocationRun).order_by(AllocationRun.created_at.desc()))
