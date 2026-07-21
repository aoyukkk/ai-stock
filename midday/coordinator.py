from __future__ import annotations

from datetime import date, datetime
from typing import Any

from midday.core import MiddayTimeGate, SHANGHAI


class MiddayAppCoordinator:
    """Application-local eligibility check; it is not an OS or global scheduler."""

    def __init__(self, service, config: dict[str, Any]) -> None:
        self.service = service
        self.config = config

    def tick(self, trade_date: date, *, now: datetime | None = None) -> dict[str, Any]:
        local = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
        if not bool(self.config.get("auto_run_midday", False)):
            return {"status": "AUTO_RUN_DISABLED", "started": False}
        gate = MiddayTimeGate(
            start_after=str(self.config.get("start_after", "11:32")),
            latest=str(self.config.get("latest_start_time", "12:50")),
        ).evaluate(trade_date, local)
        if not gate["passed"]:
            return {"status": gate["status"], "started": False}
        existing = self.service.status(trade_date=trade_date)
        if existing.get("status") not in {None, "NOT_RUN", "FAILED", "PARTIAL_SUCCESS"}:
            return {"status": "ALREADY_STARTED", "started": False, "run_id": existing.get("run_id")}
        return {"status": "STARTED", "started": True, "result": self.service.run(trade_date, decision_time=local)}
