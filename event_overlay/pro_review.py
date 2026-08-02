from __future__ import annotations

from typing import Any


def conditional_pro_candidates(rows: list[dict[str, Any]], *, max_n: int = 20) -> list[dict[str, Any]]:
    """Return only high-impact, conflict, boundary, or risk-review rows.

    Calling Pro remains an explicit runtime decision; this selector itself has
    no LLM/network side effects.
    """
    selected = []
    for row in rows:
        boundary = 18 <= int(row.get("v3_rank") or 999) <= 24
        high_impact = abs(float(row.get("event_opportunity_score") or 0)) >= 2
        conflict = str(row.get("search_status")) == "EVIDENCE_CONFLICT"
        risk_review = str(row.get("risk_action")) in {"WATCH_ONLY", "BLOCK"}
        if boundary or high_impact or conflict or risk_review:
            selected.append(row)
    return selected[:max_n]
