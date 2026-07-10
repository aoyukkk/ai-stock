from __future__ import annotations

from typing import Any

from research.fundamental import FundamentalProfile


def verified_fundamental_context(profile: FundamentalProfile | None) -> dict[str, Any]:
    """Expose only evidence-backed fields; this never computes a score bonus."""
    if profile is None or profile.verified_evidence_count <= 0:
        return {
            "fundamental_profile_verified": False,
            "fundamental_evidence_count": 0,
            "fundamental_profile": None,
            "fundamental_missing_fields": profile.missing_fields if profile else [],
        }
    verified_fields = {
        field: value
        for field, value in profile.fields.items()
        if profile.field_evidence.get(field)
    }
    return {
        "fundamental_profile_verified": True,
        "fundamental_evidence_count": profile.verified_evidence_count,
        "fundamental_profile": verified_fields,
        "fundamental_missing_fields": profile.missing_fields,
    }
