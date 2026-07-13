from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any


RETURN_BASES = {"NEXT_OPEN", "SIGNAL_CLOSE"}
SELECTION_SCOPES = {
    "FINAL_CANDIDATES", "LLM_ONLY", "MANUAL_ONLY", "BOTH_ONLY",
    "NON_ZERO_POSITION", "ALL_CANDIDATES_INCLUDING_ZERO_POSITION",
}
WEIGHTING_MODES = {"EQUAL_WEIGHT", "SUGGESTED_POSITION_WEIGHT"}


@dataclass(frozen=True)
class PerformanceRequest:
    evaluation_end_date: date
    lookback_value: int = 5
    lookback_unit: str = "TRADING_DAYS"
    start_selection_date: date | None = None
    end_selection_date: date | None = None
    return_basis: str = "NEXT_OPEN"
    selection_scope: str = "FINAL_CANDIDATES"
    weighting_mode: str = "EQUAL_WEIGHT"
    include_zero_position_stocks: bool = True
    include_risk_blocked_stocks: bool = True
    force_recalculate: bool = False

    def validate(self, max_lookback: int = 120) -> "PerformanceRequest":
        if self.lookback_unit not in {"TRADING_DAYS", "CUSTOM"}:
            raise ValueError("INVALID_LOOKBACK_UNIT")
        if not 1 <= self.lookback_value <= max_lookback:
            raise ValueError("LOOKBACK_OUT_OF_RANGE")
        if self.return_basis not in RETURN_BASES:
            raise ValueError("INVALID_RETURN_BASIS")
        if self.selection_scope not in SELECTION_SCOPES:
            raise ValueError("INVALID_SELECTION_SCOPE")
        if self.weighting_mode not in WEIGHTING_MODES:
            raise ValueError("INVALID_WEIGHTING_MODE")
        if self.lookback_unit == "CUSTOM":
            if not self.start_selection_date or not self.end_selection_date:
                raise ValueError("CUSTOM_DATE_RANGE_REQUIRED")
            if self.start_selection_date > self.end_selection_date:
                raise ValueError("START_DATE_AFTER_END_DATE")
        if self.end_selection_date and self.end_selection_date > self.evaluation_end_date:
            raise ValueError("SELECTION_DATE_AFTER_EVALUATION_END")
        return self

    def snapshot(self) -> dict[str, Any]:
        data = asdict(self)
        return {key: value.isoformat() if isinstance(value, date) else value for key, value in data.items()}


@dataclass(frozen=True)
class MarketBar:
    stock_code: str
    trade_date: date
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    pre_close: float | None
    pct_chg: float | None = None
    volume: float | None = None
    suspended: bool = False


@dataclass(frozen=True)
class MemberSnapshot:
    member_id: int
    stock_code: str
    selection_trade_date: date
    suggested_position_percent: float
