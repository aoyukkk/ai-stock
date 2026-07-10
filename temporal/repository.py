from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models.temporal import RunDataManifestRecord
from temporal.schemas import RunDataManifest


class TemporalRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def save_manifest(self, manifest: RunDataManifest) -> RunDataManifestRecord:
        request_hash = hashlib.sha256(manifest.model_dump_json().encode()).hexdigest()
        existing = self.session.scalar(select(RunDataManifestRecord).where(RunDataManifestRecord.request_hash == request_hash))
        if existing:
            return existing
        row = RunDataManifestRecord(
            manifest_id=manifest.id, run_id=manifest.run_id, run_mode=manifest.run_mode.value,
            decision_time=manifest.decision_time, base_market_trade_date=manifest.base_market_trade_date,
            target_trade_date=manifest.target_trade_date, news_cutoff_time=manifest.news_cutoff_time,
            fundamental_cutoff_time=manifest.fundamental_cutoff_time,
            required_dataset_watermarks=[item.model_dump(mode="json") for item in manifest.required_dataset_watermarks],
            optional_dataset_watermarks=[item.model_dump(mode="json") for item in manifest.optional_dataset_watermarks],
            temporal_status=manifest.temporal_status.value, actionable=manifest.actionable,
            block_reasons=manifest.block_reasons, warnings=manifest.warnings, request_hash=request_hash,
        )
        self.session.add(row); self.session.commit(); self.session.refresh(row)
        return row
