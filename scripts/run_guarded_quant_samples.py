from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from sqlalchemy import select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path: sys.path.insert(0, str(ROOT_DIR))
load_dotenv(ROOT_DIR / ".env", override=False)

from backend.core.config_manager import ConfigManager
from database.models.temporal import RunDataManifestRecord
from database.session import get_session
from fundamentals.pipeline import CachedTushareProfileService
from quant.run_repository import QuantRunRepository
from research.deepseek_unverified import DeepSeekUnverifiedResearchProvider
from research.repository import FundamentalRepository


SH = ZoneInfo("Asia/Shanghai")


def _flag(name): return os.getenv(name, "false").lower() in {"1", "true", "yes", "on"}


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--real-llm", action="store_true"); args = parser.parse_args()
    session = get_session()
    try:
        quant_repo = QuantRunRepository(session); run = quant_repo.latest_actionable()
        if run is None:
            print(json.dumps({"status": "BLOCKED", "reason": "ACTIONABLE_QUANT_RUN_REQUIRED"})); return 2
        manifest = session.scalar(select(RunDataManifestRecord).where(RunDataManifestRecord.manifest_id == run.data_manifest_id))
        samples = quant_repo.samples(run.run_id, 3)
        decision = run.decision_time.replace(tzinfo=SH) if run.decision_time.tzinfo is None else run.decision_time.astimezone(SH)
        gateway_ready = not bool(ConfigManager().get_llm_gateway_config().get("llm", {}).get("mock_only", True))
        real_allowed = all((
            bool(os.getenv("DEEPSEEK_API_KEY", "").strip()), _flag("LLM_REAL_CALLS_ENABLED"),
            _flag("RUN_REAL_FUNDAMENTAL_RESEARCH"), gateway_ready,
            manifest is not None and manifest.actionable and manifest.temporal_status in {"PASS", "PASS_WITH_WARNINGS"},
            len(samples) == 3,
        ))
        if args.real_llm and not real_allowed:
            print(json.dumps({"status": "BLOCKED", "reason": "REAL_LLM_GUARDS_NOT_SATISFIED", "quant_run_id": run.run_id})); return 2
        provider = DeepSeekUnverifiedResearchProvider(); fundamental_repo = FundamentalRepository(session); output = []
        for rank_row in samples:
            profile = CachedTushareProfileService().build(rank_row.stock_code, decision_time=decision)
            inference = provider.infer(profile.model_dump(mode="json"), use_real_llm=args.real_llm, temporal_manifest=manifest)
            request_hash = hashlib.sha256(f"{run.run_id}:{rank_row.rank}:{profile.profile_version}".encode()).hexdigest()
            fundamental_repo.save_profile({
                "stock_code": rank_row.stock_code, "version": profile.profile_version,
                "research_run_id": run.run_id,
                "profile": {**profile.model_dump(mode="json"), "unverified_inference": inference, "inference_metadata": {**provider.last_usage, "source_status": "LLM_UNVERIFIED", "input_profile_version": profile.profile_version}},
                "field_evidence": {}, "missing_fields": profile.missing_fields, "conflicts": {},
                "verified_evidence_count": 0, "suitable_for_score_boost": False,
                "field_provenance_map": {key: value.model_dump(mode="json") for key, value in profile.field_provenance_map.items()},
                "available_at": profile.available_at, "request_hash": request_hash,
            })
            output.append({
                "quant_run_id": run.run_id, "rank": rank_row.rank, "stock_code": rank_row.stock_code,
                "profile_version": profile.profile_version, "latest_financial_period": profile.latest_financial_period,
                "financial_available_at": profile.available_at, "data_age_days": profile.financial_data_age_days,
                "temporal_status": run.temporal_status, "screening_decision": "WATCH_ONLY",
                "observation_rating": inference["observation_rating"], "current_market_main_theme": inference["current_market_main_theme"],
                "model_alias": provider.last_usage.get("model_alias", "dry-run"),
                "input_tokens": provider.last_usage.get("input_tokens", 0), "output_tokens": provider.last_usage.get("output_tokens", 0),
                "cost": provider.last_usage.get("cost_usd", 0), "latency": provider.last_usage.get("latency_ms", 0),
            })
        print(json.dumps({
            "status": "COMPLETE", "real_llm": args.real_llm, "real_llm_allowed": real_allowed,
            "quant_run_id": run.run_id, "selected_ranks": [row.rank for row in samples],
            "selected_stock_codes": [row.stock_code for row in samples], "samples": output,
            "position_sizing_status": "BLOCKED_MISSING_ORDER_PLAN", "orders_created": 0,
        }, ensure_ascii=False, indent=2, default=str)); return 0
    finally: session.close()


if __name__ == "__main__": raise SystemExit(main())
