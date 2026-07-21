from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from sqlalchemy import select


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env", override=False)

from database.models.postclose_official import PostCloseOfficialRun
from database.models.quant_run import QuantRun
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
from database.session import get_session


SHANGHAI = ZoneInfo("Asia/Shanghai")


def _hash(value: Any) -> str:
    material = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconcile Flash and Pro usage into the completed post-close audit.")
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()

    session = get_session()
    try:
        row = session.scalar(select(PostCloseOfficialRun).where(PostCloseOfficialRun.run_id == args.run_id))
        if row is None or row.status not in {"POSTCLOSE_FULL_A_SUCCESS", "POSTCLOSE_FULL_A_PARTIAL_SUCCESS"}:
            raise ValueError("COMPLETED_POSTCLOSE_RUN_REQUIRED")
        report = dict(row.report_json or {})
        output_dir = ROOT / "outputs" / row.trade_date.isoformat() / "正式日线"
        main_candidates = sorted(
            output_dir.glob(f"智能交易助手_{row.trade_date.isoformat()}*.xlsx"),
            key=lambda path: path.stat().st_mtime,
        )
        if main_candidates:
            latest_main = main_candidates[-1]
            report["output_paths"] = dict(report.get("output_paths") or {}) | {
                "main_workbook": str(latest_main),
                "main_workbook_sha256": hashlib.sha256(latest_main.read_bytes()).hexdigest(),
            }
        seven_candidates = sorted(
            output_dir.glob(f"近7交易日数据对比_*_至_{row.trade_date.isoformat()}*.xlsx"),
            key=lambda path: path.stat().st_mtime,
        )
        if seven_candidates:
            latest_seven = seven_candidates[-1]
            report["seven_day"] = dict(report.get("seven_day") or {}) | {
                "workbook": str(latest_seven),
                "json": str(latest_seven.with_suffix(".json")),
                "markdown": str(latest_seven.with_suffix(".md")),
                "sha256": hashlib.sha256(latest_seven.read_bytes()).hexdigest(),
            }
            report["output_paths"] = dict(report.get("output_paths") or {}) | {
                "seven_day_workbook": str(latest_seven),
                "seven_day_json": str(latest_seven.with_suffix(".json")),
                "seven_day_markdown": str(latest_seven.with_suffix(".md")),
            }
        flash_id = str((report.get("flash") or {}).get("run_id") or "")
        pro_id = str((report.get("pro") or {}).get("run_id") or "")
        quant_id = str((report.get("quant") or {}).get("run_id") or "")
        if not flash_id or not pro_id or not quant_id:
            raise ValueError("RUN_IDENTIFIERS_MISSING")

        flash_run = session.scalar(select(ModelValidationRun).where(ModelValidationRun.run_id == flash_id))
        pro_run = session.scalar(select(ProResumeRun).where(ProResumeRun.run_id == pro_id))
        quant_run = session.scalar(select(QuantRun).where(QuantRun.run_id == quant_id))
        if flash_run is None or pro_run is None or quant_run is None:
            raise ValueError("BUSINESS_RUN_READBACK_FAILED")
        manifest = session.scalar(select(RunDataManifestRecord).where(
            RunDataManifestRecord.manifest_id == quant_run.data_manifest_id
        ))

        flash_usage = list(session.scalars(select(ModelValidationLLMAudit).where(
            ModelValidationLLMAudit.validation_run_id == flash_id
        ).order_by(ModelValidationLLMAudit.id)))
        pro_usage = list(session.scalars(select(LLMUsage).where(
            LLMUsage.pro_resume_run_id == pro_id,
            LLMUsage.usage_source == "CURRENT_CALL",
        ).order_by(LLMUsage.id)))
        samples = list(session.scalars(select(ModelValidationSample).where(
            ModelValidationSample.validation_run_id == flash_id
        ).order_by(ModelValidationSample.rank)))
        reviews = list(session.scalars(select(ProCandidateReview).where(
            ProCandidateReview.pro_resume_run_id == pro_id
        ).order_by(ProCandidateReview.pro_rank)))
        plans = list(session.scalars(select(ModelValidationOrderPlan).where(
            ModelValidationOrderPlan.validation_run_id == flash_id
        )))
        allocations = list(session.scalars(select(ModelValidationAllocation).where(
            ModelValidationAllocation.validation_run_id == flash_id
        )))

        flash_tokens = sum(int(item.input_tokens or 0) + int(item.output_tokens or 0) for item in flash_usage)
        pro_tokens = sum(int(item.total_tokens or 0) for item in pro_usage)
        report["llm_calls"] = len(flash_usage) + len(pro_usage)
        report["total_token_usage"] = flash_tokens + pro_tokens
        report["llm_usage_breakdown"] = {
            "flash_calls": len(flash_usage),
            "flash_tokens": flash_tokens,
            "pro_calls": len(pro_usage),
            "pro_tokens": pro_tokens,
            "total_calls": len(flash_usage) + len(pro_usage),
            "total_tokens": flash_tokens + pro_tokens,
            "daily_hard_limit": 5_000_000,
            "warning_threshold": 4_000_000,
            "within_budget": flash_tokens + pro_tokens < 5_000_000,
        }
        report["quant"].update({
            "factor_version": quant_run.factor_version,
            "request_hash": quant_run.request_hash,
            "config_hash": _hash(quant_run.config_snapshot),
            "input_hash": manifest.request_hash if manifest else None,
            "output_hash": report["quant"].get("hash"),
            "manifest_id": quant_run.data_manifest_id,
            "excluded_count": quant_run.skipped_count,
        })
        report["flash"].update({
            "calls": len(flash_usage),
            "tokens": flash_tokens,
            "prompt_versions": sorted({item.prompt_version for item in flash_usage}),
            "model_aliases": sorted({item.model_alias for item in flash_usage}),
            "actual_models": sorted({item.actual_model for item in flash_usage if item.actual_model}),
            "cache_status_counts": dict(Counter(item.cache_status for item in flash_usage)),
            "schema_status_counts": dict(Counter(item.schema_status for item in flash_usage)),
            "request_hash_count": sum(bool(item.request_hash) for item in flash_usage),
            "top_candidates": [
                item.stock_code for item in samples
                if bool(((item.screening_result or {}).get("_trader_demo") or {}).get("llm_selected"))
            ][:20],
        })
        report["pro"].update({
            "calls": len(pro_usage),
            "tokens": pro_tokens,
            "prompt_versions": sorted({item.prompt_version for item in pro_usage if item.prompt_version}),
            "model_aliases": sorted({item.model_alias for item in pro_usage if item.model_alias}),
            "actual_models": sorted({item.model_name for item in pro_usage if item.model_name}),
            "status_counts": dict(Counter(item.status for item in pro_usage)),
            "task_counts": dict(Counter(item.task for item in pro_usage)),
            "request_hash_count": sum(bool(item.request_hash) for item in pro_usage),
            "usage_hash": _hash([[item.call_id, item.request_hash, item.response_hash, item.total_tokens] for item in pro_usage]),
            "candidate_input_hash": _hash([item.candidate_input_hash for item in reviews]),
        })
        report["business_output_counts"] = {
            "order_plans": len(plans),
            "position_advisories": len(allocations),
            "fundamental_analyses": sum(bool(item.fundamental_result) for item in samples),
            "final_candidates": len(reviews),
            "real_orders": 0,
            "virtual_orders": 0,
        }
        warnings = list(report.get("warnings") or [])
        flash_non_pass = [item for item in flash_usage if item.schema_status != "PASS"]
        if flash_non_pass:
            warnings.append(f"FLASH_INDEPENDENT_AUDIT_EXCEPTIONS:{len(flash_non_pass)}")
        if any(item.task == "pro_portfolio_v3_repair" for item in pro_usage):
            warnings.append("PRO_PORTFOLIO_SINGLE_REPAIR_USED")
        report["warnings"] = sorted(set(warnings))
        report["reconciled_at"] = datetime.now(SHANGHAI).isoformat()
        report["reconciliation_reason"] = "INCLUDE_PRO_AUTHORITATIVE_USAGE_LEDGER_WITHOUT_RERUNNING_MODELS"

        suffix = args.run_id[-8:]
        version_number = 1
        while True:
            version = "reconciled" if version_number == 1 else f"reconciled_v{version_number}"
            if not (output_dir / f"postclose_official_2026-07-20_{suffix}_{version}.json").exists():
                break
            version_number += 1
        json_path = output_dir / f"postclose_official_2026-07-20_{suffix}_{version}.json"
        audit_path = output_dir / f"postclose_official_audit_2026-07-20_{suffix}_{version}.json"
        markdown_path = output_dir / f"postclose_official_2026-07-20_{suffix}_{version}.md"
        report["output_paths"] = dict(report.get("output_paths") or {}) | {
            "json": str(json_path),
            "audit": str(audit_path),
            "markdown": str(markdown_path),
        }
        payload = json.dumps(report, ensure_ascii=False, indent=2, default=str)
        json_path.write_text(payload, encoding="utf-8")
        audit_path.write_text(payload, encoding="utf-8")
        markdown_path.write_text("\n".join([
            "# 2026-07-20 盘后全A正式运行（完整LLM账本口径）",
            "",
            f"- Run ID：{args.run_id}",
            f"- 最终状态：{report['final_status']}",
            f"- Quant scored：{report['quant']['scored_count']}",
            f"- Flash：{report['flash']['success']} 成功 / {report['flash']['failure']} 失败",
            f"- Pro：{report['pro']['success']} 成功 / {report['pro']['failure']} 失败",
            f"- LLM Gateway：{report['llm_calls']} 次 / {report['total_token_usage']} tokens",
            "- 真实订单：0；虚拟订单：0；项目 Scheduler：关闭",
            "",
        ]), encoding="utf-8")
        row.report_json = report
        row.output_paths_json = report["output_paths"]
        row.llm_calls = int(report["llm_calls"])
        row.total_tokens = int(report["total_token_usage"])
        session.commit()
        print(json.dumps({
            "status": "RECONCILED",
            "run_id": args.run_id,
            "llm_calls": report["llm_calls"],
            "total_tokens": report["total_token_usage"],
            "json": str(json_path),
            "audit": str(audit_path),
            "markdown": str(markdown_path),
        }, ensure_ascii=True))
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
