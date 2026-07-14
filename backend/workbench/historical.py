from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from backend.core.runtime_paths import output_root
from database.models.quant_run import QuantRankResult, QuantRun
from database.models.system import LLMUsage
from database.models.temporal import RunDataManifestRecord
from database.models.validation import (
    ModelValidationAllocation,
    ModelValidationLLMAudit,
    ModelValidationOrderPlan,
    ModelValidationRun,
    ModelValidationSample,
    ProCandidateReview,
    ProResumeRun,
)
from database.models.workbench import WorkbenchRunRegistry


ROOT_DIR = Path(__file__).resolve().parents[2]


class WorkbenchResolutionError(RuntimeError):
    pass


class HistoricalPipelineRunResolver:
    """Resolve compatible persisted pipeline records without executing any pipeline stage."""

    def __init__(self, session) -> None:
        self.session = session

    def available_dates(self) -> list[dict[str, Any]]:
        dates = list(self.session.scalars(
            select(ProResumeRun.base_trade_date)
            .where(ProResumeRun.status == "COMPLETED")
            .distinct()
            .order_by(ProResumeRun.base_trade_date.desc())
        ))
        return [self._date_summary(value) for value in dates if self.compatible_runs(value)]

    def compatible_runs(self, trade_date: date) -> list[dict[str, Any]]:
        candidates = list(self.session.scalars(
            select(ProResumeRun)
            .where(ProResumeRun.base_trade_date == trade_date, ProResumeRun.status == "COMPLETED")
            .order_by(ProResumeRun.created_at.desc())
        ))
        result = []
        for pro in candidates:
            resolved = self._compatible_chain(pro)
            if resolved:
                result.append(resolved)
        return result

    def resolve(self, trade_date: date, pipeline_run_id: str | None = None) -> dict[str, Any]:
        runs = self.compatible_runs(trade_date)
        if pipeline_run_id:
            runs = [item for item in runs if item["pipeline_run_id"] == pipeline_run_id]
        if not runs:
            return self.empty_bundle(trade_date)
        return self.build_bundle(runs[0])

    def reconcile(self, trade_date: date, pipeline_run_id: str | None = None) -> dict[str, Any]:
        bundle = self.resolve(trade_date, pipeline_run_id)
        if bundle["source_mode"] == "EMPTY":
            return bundle
        key = (trade_date, bundle["pipeline_run_id"], bundle["candidate_set_hash"])
        row = self.session.scalar(select(WorkbenchRunRegistry).where(
            WorkbenchRunRegistry.trade_date == key[0],
            WorkbenchRunRegistry.pipeline_run_id == key[1],
            WorkbenchRunRegistry.candidate_set_hash == key[2],
        ))
        values = {
            "quant_run_id": bundle["quant_run_id"],
            "manifest_id": bundle["manifest_id"],
            "flash_run_id": bundle["flash_run_id"],
            "pro_run_id": bundle["pro_run_id"],
            "order_run_id": bundle["order_run_id"],
            "position_run_id": bundle["position_run_id"],
            "export_path": bundle["excel"].get("path"),
            "export_sha256": bundle["excel"].get("sha256"),
            "final_status": bundle["pipeline_status"],
            "counts": bundle["counts"],
            "validation": bundle["consistency"],
            "reconciled_at": datetime.now(timezone.utc),
        }
        if row is None:
            row = WorkbenchRunRegistry(
                trade_date=key[0], pipeline_run_id=key[1], candidate_set_hash=key[2],
                source="HISTORICAL_RECONCILIATION", **values,
            )
            self.session.add(row)
        else:
            for name, value in values.items():
                setattr(row, name, value)
        self.session.commit()
        bundle["registry_id"] = row.id
        bundle["reconciliation_source"] = row.source
        return bundle

    def build_bundle(self, chain: dict[str, Any]) -> dict[str, Any]:
        quant: QuantRun = chain["quant"]
        flash: ModelValidationRun = chain["flash"]
        pro: ProResumeRun = chain["pro"]
        samples = list(self.session.scalars(select(ModelValidationSample).where(
            ModelValidationSample.validation_run_id == flash.run_id
        )))
        sources = dict((pro.config_snapshot or {}).get("selection_sources") or {})
        sample_by_code = {row.stock_code: row for row in samples}
        candidate_codes = list((pro.config_snapshot or {}).get("candidate_codes") or sources)
        candidate_codes = list(dict.fromkeys(candidate_codes))
        meta = [((row.screening_result or {}).get("_trader_demo") or {}) for row in samples]
        quant_count = int(self.session.scalar(select(func.count()).select_from(QuantRankResult).where(
            QuantRankResult.quant_run_id == quant.run_id
        )) or 0)
        pro_count = int(self.session.scalar(select(func.count()).select_from(ProCandidateReview).where(
            ProCandidateReview.pro_resume_run_id == pro.run_id
        )) or 0)
        order_count = int(self.session.scalar(select(func.count()).select_from(ModelValidationOrderPlan).where(
            ModelValidationOrderPlan.validation_run_id == flash.run_id
        )) or 0)
        position_count = int(self.session.scalar(select(func.count()).select_from(ModelValidationAllocation).where(
            ModelValidationAllocation.validation_run_id == flash.run_id
        )) or 0)
        non_zero = int(self.session.scalar(select(func.count()).select_from(ModelValidationAllocation).where(
            ModelValidationAllocation.validation_run_id == flash.run_id,
            ModelValidationAllocation.suggested_position_percent > 0,
        )) or 0)
        manual_codes = {code for code, source in sources.items() if source in {"MANUAL", "BOTH"}}
        llm_codes = {code for code, source in sources.items() if source in {"LLM_TOP20", "BOTH"}}
        flash_success = sum(value.get("execution_status") == "SUCCESS" for value in meta)
        flash_failure = len(samples) - flash_success
        counts = {
            "quant": quant_count,
            "flash": len(samples),
            "flash_success": flash_success,
            "flash_failure": flash_failure,
            "llm_top": len(llm_codes),
            "manual": len(manual_codes),
            "both": len(manual_codes & llm_codes),
            "manual_only": len(manual_codes - llm_codes),
            "candidate": len(candidate_codes),
            "pro": pro_count,
            "order": order_count,
            "position": position_count,
            "fundamental": sum(code in sample_by_code for code in candidate_codes),
            "non_zero_position": non_zero,
        }
        excel = self._find_export(pro)
        token = self._token_ledger(quant.run_id, pro.pipeline_run_id)
        consistency = self._validate_counts(counts, excel)
        errors = self._current_errors(samples, candidate_codes)
        pipeline_status = "PARTIAL_SUCCESS" if errors or flash_failure else "COMPLETED"
        updated_at = max(value for value in (quant.updated_at, flash.updated_at, pro.updated_at) if value)
        return {
            "trade_date": pro.base_trade_date.isoformat(),
            "source_mode": "DATABASE",
            "pipeline_status": pipeline_status,
            "pipeline_run_id": pro.pipeline_run_id,
            "quant_run_id": quant.run_id,
            "manifest_id": quant.data_manifest_id,
            "flash_run_id": flash.run_id,
            "pro_run_id": pro.run_id,
            "candidate_set_hash": pro.candidate_set_hash,
            "order_run_id": f"{flash.run_id}:order" if order_count else None,
            "position_run_id": next(iter(self.session.scalars(select(ModelValidationAllocation.allocation_run_id).where(
                ModelValidationAllocation.validation_run_id == flash.run_id
            ).limit(1))), None),
            "export_id": excel.get("sha256"),
            "updated_at": updated_at,
            "counts": counts,
            "token_usage": token,
            "stages": self._stages(quant, flash, pro, counts, excel, pipeline_status),
            "current_errors": errors,
            "current_warnings": consistency["differences"],
            "resolved_historical_errors": [],
            "excel": excel,
            "consistency": consistency,
        }

    @staticmethod
    def empty_bundle(trade_date: date) -> dict[str, Any]:
        return {
            "trade_date": trade_date.isoformat(), "source_mode": "EMPTY", "pipeline_status": "EMPTY",
            "pipeline_run_id": None, "quant_run_id": None, "manifest_id": None, "flash_run_id": None,
            "pro_run_id": None, "candidate_set_hash": None, "order_run_id": None, "position_run_id": None,
            "export_id": None, "counts": {}, "token_usage": {"used": 0, "limit": 5_000_000,
            "remaining": 5_000_000, "usage_ratio": 0.0, "unavailable_usage_count": 0}, "stages": {},
            "current_errors": [], "current_warnings": [], "resolved_historical_errors": [], "excel": {},
            "consistency": {"status": "EMPTY", "differences": []},
        }

    def _compatible_chain(self, pro: ProResumeRun) -> dict[str, Any] | None:
        quant = self.session.scalar(select(QuantRun).where(QuantRun.run_id == pro.quant_run_id))
        flash = self.session.scalar(select(ModelValidationRun).where(ModelValidationRun.run_id == pro.flash_validation_run_id))
        manifest = self.session.scalar(select(RunDataManifestRecord).where(RunDataManifestRecord.manifest_id == pro.manifest_id))
        if not quant or not flash or not manifest:
            return None
        if quant.status != "COMPLETED" or quant.data_manifest_id != pro.manifest_id:
            return None
        if flash.quant_run_id != quant.run_id or flash.run_data_manifest_id != pro.manifest_id:
            return None
        pro_count = int(self.session.scalar(select(func.count()).select_from(ProCandidateReview).where(
            ProCandidateReview.pro_resume_run_id == pro.run_id
        )) or 0)
        if pro_count != pro.candidate_count:
            return None
        return {"pipeline_run_id": pro.pipeline_run_id, "quant": quant, "manifest": manifest, "flash": flash, "pro": pro}

    def _date_summary(self, trade_date: date) -> dict[str, Any]:
        runs = self.compatible_runs(trade_date)
        latest = runs[0]["pro"]
        return {
            "trade_date": trade_date.isoformat(),
            "pipeline_status": "PARTIAL_SUCCESS" if runs[0]["flash"].status == "PARTIAL_SUCCESS" else "COMPLETED",
            "pipeline_run_count": len(runs),
            "latest_completed_at": latest.updated_at or latest.created_at,
        }

    def _token_ledger(self, quant_run_id: str, pipeline_run_id: str) -> dict[str, Any]:
        validation_ids = list(self.session.scalars(select(ModelValidationRun.run_id).where(
            ModelValidationRun.quant_run_id == quant_run_id
        )))
        audit_rows = list(self.session.scalars(select(ModelValidationLLMAudit).where(
            ModelValidationLLMAudit.validation_run_id.in_(validation_ids)
        ))) if validation_ids else []
        deduped: dict[str, int] = {}
        unavailable = 0
        for row in audit_rows:
            amount = int(row.input_tokens or 0) + int(row.output_tokens or 0)
            key = row.request_hash or f"audit:{row.id}"
            deduped[key] = max(deduped.get(key, 0), amount)
            if amount == 0 and row.cache_status != "REUSED":
                unavailable += 1
        usage_rows = list(self.session.scalars(select(LLMUsage).where(LLMUsage.pipeline_run_id == pipeline_run_id)))
        pro_tokens = 0
        misc_tokens = 0
        seen = set()
        for row in usage_rows:
            key = row.call_id or row.request_hash or f"usage:{row.id}"
            if key in seen:
                continue
            seen.add(key)
            amount = int(row.total_tokens or 0)
            if row.pro_resume_run_id:
                pro_tokens += amount
            elif not row.validation_run_id:
                misc_tokens += amount
            if amount == 0 and not row.is_cached and not row.is_reused:
                unavailable += 1
        used = sum(deduped.values()) + pro_tokens + misc_tokens
        limit = 5_000_000
        return {
            "used": used, "limit": limit, "remaining": max(limit - used, 0),
            "usage_ratio": used / limit if limit else 0.0,
            "unavailable_usage_count": unavailable,
            "source": "DATABASE_DEDUPED_LEDGER",
        }

    def _find_export(self, pro: ProResumeRun) -> dict[str, Any]:
        root = output_root() / pro.base_trade_date.isoformat()
        candidates: list[Path] = []
        expected_hashes: set[str] = set()
        if root.exists():
            for audit in root.rglob("*.json"):
                try:
                    payload = json.loads(audit.read_text(encoding="utf-8"))
                except (OSError, ValueError, UnicodeError):
                    continue
                raw = json.dumps(payload, ensure_ascii=False)
                if pro.candidate_set_hash not in raw:
                    continue
                self._collect_export_metadata(payload, candidates, expected_hashes)
            candidates.extend(root.rglob("ai_trader_flash_v4_*_state_clean.xlsx"))
        for path in candidates:
            if path.is_absolute():
                resolved = path
            elif path.parts and path.parts[0].lower() == "outputs":
                resolved = output_root().parent / path
            else:
                resolved = root / path
            resolved = resolved.resolve()
            if not resolved.is_file() or root.resolve() not in resolved.parents:
                continue
            digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
            return {
                "status": "SUCCESS" if not expected_hashes or digest in expected_hashes else "WARNING",
                "path": str(resolved), "filename": resolved.name, "sha256": digest,
                "sha256_verified": not expected_hashes or digest in expected_hashes,
            }
        return {"status": "EMPTY", "path": None, "filename": None, "sha256": None, "sha256_verified": False}

    @classmethod
    def _collect_export_metadata(cls, value: Any, paths: list[Path], hashes: set[str]) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                normalized = key.lower()
                if isinstance(item, str) and item.lower().endswith(".xlsx"):
                    paths.append(Path(item))
                if isinstance(item, str) and "sha256" in normalized and len(item) == 64:
                    hashes.add(item.lower())
                cls._collect_export_metadata(item, paths, hashes)
        elif isinstance(value, list):
            for item in value:
                cls._collect_export_metadata(item, paths, hashes)

    @staticmethod
    def _validate_counts(counts: dict[str, int], excel: dict[str, Any]) -> dict[str, Any]:
        differences = []
        expected_pairs = (("candidate", "pro"), ("candidate", "order"), ("candidate", "position"), ("candidate", "fundamental"))
        for expected, actual in expected_pairs:
            if counts[expected] != counts[actual]:
                differences.append({"expected": expected, "actual": actual, "expected_count": counts[expected], "actual_count": counts[actual]})
        if counts["candidate"] != counts["llm_top"] + counts["manual"] - counts["both"]:
            differences.append({"expected": "deduplicated_top_plus_manual", "actual": "candidate"})
        if excel.get("status") == "WARNING":
            differences.append({"expected": "excel_sha256_match", "actual": "mismatch"})
        return {"status": "PASS" if not differences else "WARNING", "differences": differences}

    @staticmethod
    def _current_errors(samples: list[ModelValidationSample], candidate_codes: list[str]) -> list[dict[str, Any]]:
        del candidate_codes
        errors = []
        for row in samples:
            fundamental = row.fundamental_result or {}
            screening = row.screening_result or {}
            meta = screening.get("_trader_demo") or {}
            if meta.get("execution_status") == "SUCCESS":
                continue
            errors.append({
                "stock_code": row.stock_code,
                "stage": meta.get("failed_stage") or "FUNDAMENTAL_V4",
                "error_category": meta.get("error_category") or fundamental.get("error_category") or "PERSISTED_STAGE_FAILURE",
            })
        return errors

    @staticmethod
    def _stages(quant, flash, pro, counts, excel, pipeline_status) -> dict[str, Any]:
        final_status = "COMPLETED" if counts["pro"] and counts["order"] and counts["position"] else "NOT_RUN"
        stages = {
            "data": {"status": "READY", "count": quant.scored_count, "run_id": quant.data_manifest_id, "updated_at": quant.updated_at, "temporal_gate": quant.temporal_status, "coverage": 1.0},
            "quant": {"status": "COMPLETED", "count": counts["quant"], "run_id": quant.run_id, "updated_at": quant.updated_at},
            "flash": {"status": "PARTIAL_SUCCESS" if counts["flash_failure"] else "COMPLETED", "count": counts["flash"], "success_count": counts["flash_success"], "failure_count": counts["flash_failure"], "run_id": flash.run_id, "updated_at": flash.updated_at},
            "manual": {"status": "LOADED" if counts["manual"] else "NOT_RUN", "count": counts["manual"], "run_id": flash.run_id, "updated_at": flash.updated_at},
            "final": {"status": final_status, "count": counts["candidate"], "run_id": pro.run_id, "updated_at": pro.updated_at},
            "export": {"status": excel.get("status", "EMPTY"), "count": 1 if excel.get("path") else 0, "run_id": excel.get("sha256"), "updated_at": pro.updated_at},
        }
        if stages["export"]["status"] == "SUCCESS" and stages["final"]["status"] == "NOT_RUN":
            raise WorkbenchResolutionError("WORKBENCH_STAGE_STATE_INCONSISTENT")
        if pipeline_status == "PARTIAL_SUCCESS" and stages["flash"]["status"] == "NOT_RUN":
            raise WorkbenchResolutionError("WORKBENCH_STAGE_STATE_INCONSISTENT")
        return stages
