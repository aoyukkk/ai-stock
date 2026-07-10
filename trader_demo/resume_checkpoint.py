from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from database.models.quant_run import QuantRun
from database.models.temporal import RunDataManifestRecord
from database.models.validation import ModelValidationRun, ModelValidationSample
from stock_codes import normalize_ts_code
from trader_demo.pro_resume import chunk_candidates, stable_candidate_order


QUANT_RUN_ID = "quant-840384b9e637e1143f243083"
FLASH_VALIDATION_RUN_ID = "trader-demo-1ee21177ee9e48908cf4"
MANUAL_CODES = [
    "300145.SZ", "300821.SZ", "301151.SZ", "301356.SZ",
    "603726.SH", "603019.SH", "002409.SZ",
]


@dataclass(frozen=True)
class ResumeContext:
    quant_run: QuantRun
    manifest: RunDataManifestRecord
    flash_run: ModelValidationRun
    samples: list[ModelValidationSample]
    top20_codes: list[str]
    manual_codes: list[str]
    candidates: list[ModelValidationSample]
    candidate_codes: list[str]
    selection_sources: dict[str, str]
    top20_hash: str
    manual_hash: str
    candidate_set_hash: str


def load_resume_context(session, flash_validation_run_id: str | None = None) -> ResumeContext:
    quant_run = session.scalar(select(QuantRun).where(QuantRun.run_id == QUANT_RUN_ID))
    if quant_run is None:
        raise ValueError("RESUME_QUANT_RUN_MISSING")
    manifest = session.scalar(
        select(RunDataManifestRecord).where(RunDataManifestRecord.manifest_id == quant_run.data_manifest_id)
    )
    flash_run = session.scalar(
        select(ModelValidationRun).where(
            ModelValidationRun.run_id == (flash_validation_run_id or FLASH_VALIDATION_RUN_ID)
        )
    )
    if manifest is None or flash_run is None:
        raise ValueError("RESUME_MANIFEST_OR_FLASH_RUN_MISSING")
    if (
        quant_run.base_market_trade_date.isoformat() != "2026-07-10"
        or quant_run.target_trade_date.isoformat() != "2026-07-13"
        or quant_run.scored_count != 5308
        or not quant_run.no_llm_call_verified
        or quant_run.per_stock_api_call_count != 0
        or quant_run.temporal_status != "PASS"
        or not quant_run.actionable
        or manifest.manifest_id != flash_run.run_data_manifest_id
        or flash_run.quant_run_id != quant_run.run_id
    ):
        raise ValueError("RESUME_BASELINE_MISMATCH")
    samples = list(session.scalars(
        select(ModelValidationSample)
        .where(ModelValidationSample.validation_run_id == flash_run.run_id)
        .order_by(ModelValidationSample.rank)
    ))
    if len(samples) != 105:
        raise ValueError("RESUME_FLASH_SAMPLE_COUNT_MISMATCH")
    top20 = [sample for sample in samples if (sample.screening_result or {}).get("_trader_demo", {}).get("llm_selected")]
    manual = [sample for sample in samples if (sample.screening_result or {}).get("_trader_demo", {}).get("manual_selected")]
    candidates = [sample for sample in samples if (sample.screening_result or {}).get("_trader_demo", {}).get("selection_source")]
    expected_candidates = len({normalize_ts_code(sample.stock_code) for sample in [*top20, *manual]})
    if len(top20) != 20 or len(manual) != 7 or len(candidates) != expected_candidates:
        raise ValueError("RESUME_CANDIDATE_COUNTS_MISMATCH")
    if {normalize_ts_code(sample.stock_code) for sample in manual} != set(MANUAL_CODES):
        raise ValueError("RESUME_MANUAL_SET_MISMATCH")
    ordered = stable_candidate_order(candidates)
    sources = {
        normalize_ts_code(sample.stock_code): str(
            (sample.screening_result or {}).get("_trader_demo", {}).get("selection_source")
        )
        for sample in ordered
    }
    top20_codes = sorted(normalize_ts_code(sample.stock_code) for sample in top20)
    candidate_codes = [normalize_ts_code(sample.stock_code) for sample in ordered]
    return ResumeContext(
        quant_run=quant_run, manifest=manifest, flash_run=flash_run, samples=samples,
        top20_codes=top20_codes, manual_codes=list(MANUAL_CODES), candidates=ordered,
        candidate_codes=candidate_codes, selection_sources=sources,
        top20_hash=_hash(top20_codes), manual_hash=_hash(MANUAL_CODES),
        candidate_set_hash=_hash([{"stock_code": code, "selection_source": sources[code]} for code in candidate_codes]),
    )


def validate_or_upgrade_checkpoint(path: Path, context: ResumeContext, *, chunk_size: int) -> dict[str, Any]:
    if not path.exists():
        raise ValueError(f"RESUME_CHECKPOINT_NOT_FOUND:{path}")
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError("RESUME_CHECKPOINT_INVALID") from exc
    expected = {
        "quant_run_id": context.quant_run.run_id,
        "manifest_id": context.manifest.manifest_id,
        "flash_validation_run_id": context.flash_run.run_id,
        "top20_hash": context.top20_hash,
        "manual_hash": context.manual_hash,
        "candidate_set_hash": context.candidate_set_hash,
    }
    resume = existing.get("resume") or {}
    for key, value in expected.items():
        if key in resume and resume[key] != value:
            raise ValueError("CHECKPOINT_CANDIDATE_HASH_MISMATCH")
    chunks = chunk_candidates(context.candidates, chunk_size)
    upgraded = {
        "stage": "PRO_AGGREGATION",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "resume": {
            **expected,
            "base_trade_date": context.quant_run.base_market_trade_date.isoformat(),
            "target_trade_date": context.quant_run.target_trade_date.isoformat(),
            "top20_count": 20, "manual_count": 7, "candidate_count": len(context.candidates),
            "top20_codes": context.top20_codes, "manual_codes": context.manual_codes,
            "candidate_codes": context.candidate_codes,
            "pro_contract_version": "pro_candidate_wire_v2",
            "candidate_prompt_version": "pro_candidate_review_v2",
            "portfolio_prompt_version": "pro_portfolio_summary_v2",
            "chunk_size": chunk_size,
            "chunks": [
                {
                    "chunk_id": f"candidate-{index:02d}",
                    "stock_codes": [normalize_ts_code(sample.stock_code) for sample in chunk],
                }
                for index, chunk in enumerate(chunks, start=1)
            ],
            "quant_rerun": False, "flash_rerun": False,
        },
        "legacy_checkpoint": {
            "stage": existing.get("stage"), "updated_at": existing.get("updated_at"),
        },
    }
    path.write_text(json.dumps(upgraded, ensure_ascii=False, indent=2), encoding="utf-8")
    return upgraded


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
