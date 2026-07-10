from __future__ import annotations

from database.base import Base
from database.session import create_engine_from_url, get_session
from research.repository import FundamentalRepository


def test_profile_persists_versions_and_pending_tasks_are_idempotent():
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    repo = FundamentalRepository(session)
    base = {"stock_code": "000001.SZ", "research_run_id": "r1", "profile": {}, "field_evidence": {}, "missing_fields": [], "conflicts": {}, "verified_evidence_count": 0, "suitable_for_score_boost": False, "field_provenance_map": {}}
    repo.save_profile({**base, "version": "v1"})
    repo.save_profile({**base, "version": "v2"})
    assert repo.latest_profile("000001.SZ").version == "v2"
    assert repo.upsert_verification_tasks("000001.SZ", {"industry_chain": {"value": "x"}}) == 1
    assert repo.upsert_verification_tasks("000001.SZ", {"industry_chain": {"value": "y"}}) == 0
    session.close()
    session2 = get_session(engine)
    assert FundamentalRepository(session2).latest_profile("000001.SZ").version == "v2"
    session2.close()
    engine.dispose()
