from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from database.models.research import (
    FundamentalResearchRun,
    PendingVerificationTask,
    ResearchEvidenceRecord,
    StockFundamentalProfile,
)
from research.schemas import ResearchEvidence


class FundamentalRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def save_run(self, payload: dict[str, Any]) -> FundamentalResearchRun:
        request_hash = payload.get("request_hash")
        if request_hash:
            existing = self.session.scalar(
                select(FundamentalResearchRun).where(FundamentalResearchRun.request_hash == request_hash)
            )
            if existing:
                return existing
        row = FundamentalResearchRun(**payload)
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row

    def save_profile(self, payload: dict[str, Any]) -> StockFundamentalProfile:
        existing = self.session.scalar(
            select(StockFundamentalProfile).where(
                StockFundamentalProfile.stock_code == payload["stock_code"],
                StockFundamentalProfile.version == payload["version"],
            )
        )
        if existing:
            return existing
        self.session.execute(
            update(StockFundamentalProfile)
            .where(StockFundamentalProfile.stock_code == payload["stock_code"])
            .values(is_current=False)
        )
        row = StockFundamentalProfile(**payload, is_current=True)
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row

    def latest_profile(self, stock_code: str) -> StockFundamentalProfile | None:
        return self.session.scalar(
            select(StockFundamentalProfile)
            .where(StockFundamentalProfile.stock_code == stock_code, StockFundamentalProfile.is_current.is_(True))
            .order_by(StockFundamentalProfile.created_at.desc())
        )

    def save_evidence(self, run_id: str, evidence: list[ResearchEvidence]) -> int:
        inserted = 0
        for item in evidence:
            existing = self.session.scalar(
                select(ResearchEvidenceRecord).where(
                    ResearchEvidenceRecord.run_id == run_id,
                    ResearchEvidenceRecord.content_hash == item.content_hash,
                )
            )
            if existing:
                continue
            payload = item.model_dump(exclude={"source_temporal_status", "query_time"})
            self.session.add(ResearchEvidenceRecord(run_id=run_id, **payload))
            inserted += 1
        self.session.commit()
        return inserted

    def upsert_verification_tasks(self, stock_code: str, fields: dict[str, Any]) -> int:
        count = 0
        for field_name, value in fields.items():
            existing = self.session.scalar(
                select(PendingVerificationTask).where(
                    PendingVerificationTask.stock_code == stock_code,
                    PendingVerificationTask.field_name == field_name,
                    PendingVerificationTask.status == "PENDING",
                )
            )
            if existing:
                existing.unverified_value = value
                existing.retry_after = datetime.now(timezone.utc) + timedelta(days=1)
            else:
                self.session.add(PendingVerificationTask(
                    stock_code=stock_code,
                    field_name=field_name,
                    unverified_value=value,
                    retry_after=datetime.now(timezone.utc) + timedelta(days=1),
                    preferred_source_types=["company_filing", "exchange", "mainstream_financial_media"],
                ))
                count += 1
        self.session.commit()
        return count
