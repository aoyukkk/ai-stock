from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request

from backend.core.responses import success_response
from backend.core.runtime_paths import report_root
from datasource.ifind.probe import load_latest_audit


router = APIRouter(prefix="/api/data-sources/ifind", tags=["ifind-capability-audit"])


@router.get("/capabilities/latest")
def latest_capabilities(request: Request) -> dict:
    payload = load_latest_audit(Path(report_root()) / "ifind")
    if payload is None:
        payload = {
            "overall_status": "NOT_TESTED",
            "environment": {"sdk_status": "UNKNOWN"},
            "authentication": {"credential_status": "UNKNOWN", "login_status": "UNKNOWN"},
            "capabilities": [],
            "call_summary": {"actual_calls": 0},
        }
    return success_response(data=payload, trace_id=request.state.trace_id)
