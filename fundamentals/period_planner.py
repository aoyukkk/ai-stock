from __future__ import annotations

import calendar
from datetime import date, datetime, timezone
from typing import Any

from fundamentals.revision import select_latest_revisions


QUARTER_ENDS = ((3, 31), (6, 30), (9, 30), (12, 31))


class FinancialPeriodPlanner:
    def __init__(self, latest_periods: int = 8) -> None:
        self.latest_periods = latest_periods

    def candidate_periods(self, decision_time: datetime) -> list[str]:
        if decision_time.tzinfo is None:
            raise ValueError("decision_time must be timezone-aware")
        today = decision_time.date()
        values = []
        for year in range(today.year, today.year - 4, -1):
            for month, day in reversed(QUARTER_ENDS):
                period = date(year, month, day)
                if period <= today:
                    values.append(period.strftime("%Y%m%d"))
        return values[: self.latest_periods]

    def select_latest(
        self,
        records_by_period: dict[str, list[dict[str, Any]]],
        stock_code: str,
        decision_time: datetime,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        candidates = []
        for period, records in records_by_period.items():
            stock_rows = [row for row in records if row.get("ts_code") == stock_code]
            candidates.extend(select_latest_revisions(stock_rows, decision_time))
        if not candidates:
            return {}, {"selection_reason": "NO_DISCLOSED_REPORT", "is_latest_available_as_of_decision_time": False}
        selected = max(candidates, key=lambda row: (str(row.get("end_date") or ""), str(row.get("available_at") or "")))
        available = datetime.fromisoformat(selected["available_at"])
        age = max(0, (decision_time.astimezone(timezone.utc) - available.astimezone(timezone.utc)).days)
        candidate_periods = self.candidate_periods(decision_time)
        latest_expected = candidate_periods[0] if candidate_periods else str(selected.get("end_date") or "")
        selected_period = str(selected.get("end_date") or "")
        return selected, {
            "latest_financial_period": selected_period,
            "report_type": str(selected.get("report_type") or "FORMAL"),
            "announcement_date": selected.get("f_ann_date") or selected.get("ann_date"),
            "available_at": selected["available_at"],
            "data_age_days": age,
            "is_latest_available_as_of_decision_time": True,
            "stale_reason": "FUNDAMENTAL_DATA_STALE" if selected_period < latest_expected else None,
            "selection_reason": "LATEST_DISCLOSED_FORMAL_REPORT",
        }
