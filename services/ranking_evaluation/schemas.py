from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True)
class RankingSourceRow:
    stock_code: str
    original_rank: int
    quant_score: float | None
    stock_name: str = ""
    ts_code: str = ""
    source_row_number: int = 0
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DailyPrice:
    stock_code: str
    trade_date: date
    close: float | None
    volume: float | None
    source: str
    source_hash: str
    suspended: bool = False


@dataclass(frozen=True)
class SnapshotCaptureRequest:
    ranking_trade_date: date
    source_quant_run_id: str
    factor_version: str
    evaluation_scope: str
    allow_historical_import: bool = False
    snapshot_origin: str = "FORWARD_CAPTURE"
    generated_at: datetime | None = None


@dataclass(frozen=True)
class WeeklyEvaluationRequest:
    week_ending: date
    factor_version: str
    evaluation_version: str
