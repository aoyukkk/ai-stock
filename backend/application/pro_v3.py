from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select

from database.models.quant_run import QuantRun
from database.models.temporal import RunDataManifestRecord
from database.models.validation import ModelValidationRun, ModelValidationSample, ProResumeRun
from stock_codes import normalize_ts_code
from trader_demo.pro_single_v3 import (
    PORTFOLIO_PROMPT_VERSION,
    RANKING_VERSION,
    SINGLE_CONTRACT_VERSION,
    SINGLE_PROMPT_VERSION,
    ProSingleV3Service,
    ensure_v3_schema,
    select_v3_canary,
    stable_v3_order,
)
from trader_demo.runtime import temporary_real_llm_runtime
from trader_demo.service import TraderDemoService
from trader_demo.usage_ledger import AuthoritativeUsageLedger


@dataclass(frozen=True)
class ProContext:
    quant_run: QuantRun
    manifest: RunDataManifestRecord
    flash_run: ModelValidationRun
    candidates: list[ModelValidationSample]
    selection_sources: dict[str, str]
    top_hash: str
    manual_hash: str
    candidate_hash: str


class ProductionProV3ApplicationService:
    def __init__(self, session, output_root: Path) -> None:
        self.session = session
        self.output_root = output_root

    def run(self, flash_run_id: str, *, account_equity, available_cash) -> dict[str, Any]:
        context = self._context(flash_run_id)
        existing = self.session.scalar(select(ProResumeRun).where(
            ProResumeRun.flash_validation_run_id == flash_run_id,
            ProResumeRun.candidate_set_hash == context.candidate_hash,
            ProResumeRun.pro_contract_version == SINGLE_CONTRACT_VERSION,
            ProResumeRun.status == "COMPLETED",
        ).order_by(ProResumeRun.id.desc()))
        if existing is not None:
            return {"pro_run_id": existing.run_id, "pipeline_run_id": existing.pipeline_run_id, "candidate_count": existing.candidate_count, "reused": True}

        resume = self._create_resume(context)
        checkpoint = self.output_root / context.quant_run.base_market_trade_date.isoformat() / "checkpoints" / f"{resume.run_id}.json"
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        checkpoint.write_text(json.dumps({"stage": "PRO_V3_PENDING", "run_id": resume.run_id}, ensure_ascii=False), encoding="utf-8")
        ledger = AuthoritativeUsageLedger(self.session.get_bind(), pipeline_run_id=resume.pipeline_run_id)
        with temporary_real_llm_runtime():
            service = ProSingleV3Service(self.session, ledger)
            ordered = stable_v3_order(context.candidates)
            service.run_canary(resume, select_v3_canary(ordered), context.selection_sources, checkpoint)
            service.run_reviews(resume, ordered, context.selection_sources, checkpoint, stage="FULL", stop_on_failure=True)
            reviews = service.rank_and_apply(resume, ordered, context.selection_sources)
            service.run_portfolio(resume, ordered, reviews, context.selection_sources)
        TraderDemoService(self.session).generate_candidate_outputs(
            flash_run_id,
            account_equity=account_equity,
            available_cash=available_cash,
        )
        resume.status = "COMPLETED"
        self.session.commit()
        return {
            "pro_run_id": resume.run_id,
            "pipeline_run_id": resume.pipeline_run_id,
            "candidate_count": len(context.candidates),
            "ranking_version": RANKING_VERSION,
            "reused": False,
        }

    def _context(self, flash_run_id: str) -> ProContext:
        flash = self.session.scalar(select(ModelValidationRun).where(ModelValidationRun.run_id == flash_run_id))
        if flash is None or flash.status not in {"COMPLETED", "SUCCESS", "PARTIAL_SUCCESS"}:
            raise ValueError("COMPLETED_FLASH_RUN_REQUIRED")
        quant = self.session.scalar(select(QuantRun).where(QuantRun.run_id == flash.quant_run_id))
        manifest = self.session.scalar(select(RunDataManifestRecord).where(RunDataManifestRecord.manifest_id == flash.run_data_manifest_id))
        if quant is None or manifest is None or not quant.actionable or not manifest.actionable:
            raise ValueError("ACTIONABLE_TEMPORAL_CONTEXT_REQUIRED")
        samples = list(self.session.scalars(select(ModelValidationSample).where(
            ModelValidationSample.validation_run_id == flash_run_id
        ).order_by(ModelValidationSample.rank)))
        candidates = [sample for sample in samples if (sample.screening_result or {}).get("_trader_demo", {}).get("selection_source")]
        if len(candidates) < 3:
            raise ValueError("PRO_V3_CANDIDATES_INSUFFICIENT")
        ordered = stable_v3_order(candidates)
        sources = {normalize_ts_code(item.stock_code): str(item.screening_result["_trader_demo"]["selection_source"]) for item in ordered}
        top = sorted(normalize_ts_code(item.stock_code) for item in samples if (item.screening_result or {}).get("_trader_demo", {}).get("llm_selected"))
        manual = sorted(normalize_ts_code(item.stock_code) for item in samples if (item.screening_result or {}).get("_trader_demo", {}).get("manual_selected"))
        candidate_material = [{"stock_code": normalize_ts_code(item.stock_code), "selection_source": sources[normalize_ts_code(item.stock_code)]} for item in ordered]
        return ProContext(quant, manifest, flash, ordered, sources, _hash(top), _hash(manual), _hash(candidate_material))

    def _create_resume(self, context: ProContext) -> ProResumeRun:
        ensure_v3_schema(self.session.get_bind())
        prior = self.session.scalar(select(ProResumeRun).where(
            ProResumeRun.flash_validation_run_id == context.flash_run.run_id,
            ProResumeRun.candidate_set_hash == context.candidate_hash,
        ).order_by(ProResumeRun.id.desc()))
        pipeline_id = f"daily-{context.quant_run.base_market_trade_date:%Y%m%d}-{uuid.uuid4().hex[:12]}"
        row = ProResumeRun(
            run_id=f"pro-resume-{uuid.uuid4().hex[:20]}", pipeline_run_id=pipeline_id,
            quant_run_id=context.quant_run.run_id, manifest_id=context.manifest.manifest_id,
            flash_validation_run_id=context.flash_run.run_id, previous_failed_run_id=prior.run_id if prior else None,
            pro_contract_version=SINGLE_CONTRACT_VERSION, prompt_version=SINGLE_PROMPT_VERSION,
            portfolio_prompt_version=PORTFOLIO_PROMPT_VERSION,
            base_trade_date=context.quant_run.base_market_trade_date, target_trade_date=context.quant_run.target_trade_date,
            top20_hash=context.top_hash, manual_hash=context.manual_hash, candidate_set_hash=context.candidate_hash,
            candidate_count=len(context.candidates), chunk_size=1, chunk_count=len(context.candidates), status="RUNNING",
            config_snapshot={"candidate_mode": "SINGLE_STOCK", "candidate_codes": [normalize_ts_code(item.stock_code) for item in context.candidates], "selection_sources": context.selection_sources},
            portfolio_result={}, warnings=["ADVISORY_ONLY", "NON_ACTIONABLE"],
        )
        self.session.add(row)
        self.session.commit()
        return row


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

