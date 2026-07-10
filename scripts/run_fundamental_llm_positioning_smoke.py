from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.core.config import get_app_config
from database.models.factor import StockFactorScore
from database.session import get_session, init_db
from fundamentals.pipeline import CachedTushareProfileService
from position_sizing.engine import PositionSizingEngine
from position_sizing.repository import AllocationRepository
from position_sizing.schemas import AccountState, SizingCandidate
from research.deepseek_unverified import DeepSeekUnverifiedResearchProvider
from research.repository import FundamentalRepository


def _flag(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on"}


def _select_samples(session, count: int) -> list[tuple[str, int]]:
    latest = session.scalar(select(func.max(StockFactorScore.date)))
    if latest:
        rows = session.scalars(
            select(StockFactorScore).where(StockFactorScore.date == latest).order_by(StockFactorScore.total_score.desc())
        ).all()
        if rows:
            indexes = sorted(set((0, len(rows) // 2, len(rows) - 1)))[:count]
            return [(rows[index].stock_code, index + 1) for index in indexes]
    fixtures = ["000001.SZ", "600000.SH", "688001.SH"]
    return [(code, rank) for rank, code in enumerate(fixtures[:count], 1)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample-size", type=int, default=3, choices=range(1, 6))
    parser.add_argument("--real-llm", action="store_true")
    args = parser.parse_args()
    get_app_config()  # loads local .env without exposing it
    real_allowed = all((
        bool(os.getenv("DEEPSEEK_API_KEY", "").strip()),
        _flag("LLM_REAL_CALLS_ENABLED"),
        _flag("RUN_REAL_FUNDAMENTAL_RESEARCH"),
    ))
    if args.real_llm and not real_allowed:
        print(json.dumps({"status": "BLOCKED", "reason": "REAL_LLM_GUARDS_NOT_SATISFIED"}))
        return 2
    init_db()
    session = get_session()
    try:
        samples = _select_samples(session, args.sample_size)
        fundamental_repo = FundamentalRepository(session)
        provider = DeepSeekUnverifiedResearchProvider()
        screening = []
        candidates = []
        for code, rank in samples:
            profile = CachedTushareProfileService().build(code)
            inference = provider.infer(profile.model_dump(mode="json"), use_real_llm=args.real_llm)
            profile_hash = hashlib.sha256(f"smoke:{profile.profile_version}:{code}".encode()).hexdigest()
            fundamental_repo.save_profile({
                "stock_code": code, "version": profile.profile_version, "research_run_id": "smoke",
                "profile": {
                    **profile.model_dump(mode="json"),
                    "unverified_inference": inference,
                    "inference_metadata": {
                        **provider.last_usage,
                        "source_status": "LLM_UNVERIFIED",
                        "fallback_reason": "SEARCH_PROVIDERS_NOT_CONFIGURED",
                    },
                },
                "field_evidence": {}, "missing_fields": profile.missing_fields, "conflicts": {},
                "verified_evidence_count": 0, "suitable_for_score_boost": False,
                "field_provenance_map": {"unverified_inference": {"source_status": "LLM_UNVERIFIED", "display_marker": "*"}},
                "available_at": profile.available_at, "request_hash": profile_hash,
            })
            fundamental_repo.upsert_verification_tasks(code, {
                key: value
                for key, value in inference.items()
                if key not in {"stock_code", "as_of_time", "research_mode", "financial_status", "missing_fields", "data_conflict", "requires_manual_review", "display_marker"}
            })
            statuses = [item.source_status.value for item in profile.field_provenance_map.values()]
            screening.append({
                "stock_code": code, "quant_rank": rank, "profile_version": profile.profile_version,
                "verified_field_count": statuses.count("VERIFIED_STRUCTURED"),
                "derived_field_count": statuses.count("DERIVED_RULE"),
                "unverified_field_count": len([key for key in inference if key not in {"stock_code", "as_of_time", "research_mode", "financial_status", "missing_fields", "data_conflict", "requires_manual_review", "display_marker"}]),
                "unknown_field_count": statuses.count("UNKNOWN"),
                "observation_rating": inference["observation_rating"], "screening_decision": "WATCH_ONLY",
                "confidence": 0.25, "data_conflict": False, "model_alias": "search-analysis-fast" if args.real_llm else "dry-run",
                "token": provider.last_usage.get("input_tokens", 0) + provider.last_usage.get("output_tokens", 0),
                "cost": provider.last_usage.get("cost_usd", 0), "latency": provider.last_usage.get("latency_ms", 0),
            })
            candidates.append(SizingCandidate(
                stock_code=code, final_score=Decimal("70"), controller_confidence=Decimal("0.7"),
                data_quality_factor=Decimal("0.7"), risk_gate_factor=Decimal("1"),
                entry_price=Decimal("10"), stop_price=Decimal("9.3"), max_acceptable_price=Decimal("10.2"),
                risk_reward=Decimal("2"), average_daily_amount=Decimal("50000000"),
                unverified_fundamental_research=True,
                risk_level=profile.financial_status["status"],
            ))
        result = PositionSizingEngine().evaluate(
            AccountState(equity=Decimal("1000000"), available_cash=Decimal("800000")), candidates
        )
        request_hash = hashlib.sha256(json.dumps({
            "engine_version": result.version,
            "candidates": [item.model_dump(mode="json") for item in candidates],
        }, sort_keys=True).encode()).hexdigest()
        allocation = AllocationRepository(session).save({
            "run_id": "smoke-" + request_hash[:20], "engine_version": result.version,
            "advisory_only": True, "dry_run": not args.real_llm, "config_snapshot": {},
            "total_suggested_capital": result.total_suggested_capital,
            "total_maximum_planned_loss": result.total_maximum_planned_loss,
            "request_hash": request_hash,
        }, result)
        print(json.dumps({
            "status": "COMPLETE", "dry_run": not args.real_llm, "sample_size": len(samples),
            "secret_status": {"tushare": "CONFIGURED" if os.getenv("TUSHARE_TOKEN", "") else "NOT_CONFIGURED", "deepseek": "CONFIGURED" if os.getenv("DEEPSEEK_API_KEY", "") else "NOT_CONFIGURED"},
            "screening": screening, "allocation_run_id": allocation.run_id,
            "suggestions": [item.model_dump(mode="json") for item in result.suggestions],
            "orders_created": 0, "real_trading_enabled": False,
        }, ensure_ascii=False, default=str, indent=2))
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
