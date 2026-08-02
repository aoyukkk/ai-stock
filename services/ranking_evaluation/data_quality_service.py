from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import uuid4

from sqlalchemy import select

from database.models.ranking_evaluation import RankingEvaluationDataIssue
from services.ranking_evaluation.utils import stable_hash


LEVEL_ORDER = {"NORMAL": 0, "WARNING": 1, "ABNORMAL": 2}


class RankingDataQualityService:
    def __init__(self, session) -> None:
        self.session = session

    def record(
        self,
        *,
        issue_code: str,
        issue_level: str,
        detail: str,
        affected_date: date | None = None,
        affected_stock: str | None = None,
        affected_version: str | None = None,
        snapshot_id: int | None = None,
        weekly_run_id: int | None = None,
    ) -> RankingEvaluationDataIssue:
        detected_at = datetime.now(timezone.utc)
        material = {
            "issue_code": issue_code,
            "issue_level": issue_level,
            "detail": detail,
            "affected_date": affected_date,
            "affected_stock": affected_stock,
            "affected_version": affected_version,
            "snapshot_id": snapshot_id,
            "weekly_run_id": weekly_run_id,
        }
        issue_hash = stable_hash(material)
        existing = self.session.scalar(
            select(RankingEvaluationDataIssue).where(
                RankingEvaluationDataIssue.issue_hash == issue_hash
            )
        )
        if existing:
            return existing
        row = RankingEvaluationDataIssue(
            issue_id=f"rank-issue-{uuid4().hex}",
            issue_hash=issue_hash,
            snapshot_id=snapshot_id,
            weekly_run_id=weekly_run_id,
            issue_code=issue_code,
            issue_level=issue_level,
            affected_date=affected_date,
            affected_stock=affected_stock,
            affected_version=affected_version,
            detail=detail,
            detected_at=detected_at,
        )
        self.session.add(row)
        return row

    def status(self, issues: list[RankingEvaluationDataIssue]) -> str:
        if not issues:
            return "NORMAL"
        return max(
            (row.issue_level for row in issues),
            key=lambda value: LEVEL_ORDER.get(value, 2),
        )
