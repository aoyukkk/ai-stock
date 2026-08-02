from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.models.validation import ModelValidationFailureAudit, ModelValidationSample
from database.session import get_session, init_db


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply an evidence-bounded human correction to one Flash sample.")
    parser.add_argument("--validation-run", required=True)
    parser.add_argument("--stock-code", required=True)
    parser.add_argument("--audit-output", type=Path, required=True)
    args = parser.parse_args()

    init_db()
    session = get_session()
    try:
        sample = session.scalar(select(ModelValidationSample).where(
            ModelValidationSample.validation_run_id == args.validation_run,
            ModelValidationSample.stock_code == args.stock_code,
        ))
        if sample is None:
            raise RuntimeError("HUMAN_CORRECTION_SAMPLE_NOT_FOUND")

        original_screening = dict(sample.screening_result or {})
        original_fundamental = dict(sample.fundamental_result or {})
        original_errors = list((original_screening.get("_trader_demo") or {}).get("errors") or [])

        fundamental = dict(original_fundamental)
        fundamental.update({
            "analysis_status": "MANUAL_COMPLETED",
            "industry_chain": {
                "chain_name": "房地产开发与物业服务产业链",
                "chain_position": "MULTI_SEGMENT",
                "source_status": "HUMAN_REVIEW_FROM_VERIFIED_BUSINESS",
            },
            "industry_position": {
                "description": "央企房地产开发商，业务覆盖开发、销售、租赁及物业管理；未补充未经核验的市场份额或排名。",
                "source_status": "HUMAN_REVIEW_FROM_VERIFIED_BUSINESS",
            },
            "competitive_advantage": {
                "summary": "央企背景、规模化开发及综合运营能力；未补充未经核验的市场份额或客户数据。",
                "source_status": "HUMAN_REVIEW_FROM_VERIFIED_BUSINESS",
            },
            "industry_trend": {
                "summary": "房地产行业处于调整期，政策、销售恢复和现金流是主要变量；仅作行业层面判断。",
                "source_status": "HUMAN_REVIEW_GENERAL_INDUSTRY",
            },
            "investment_logic": {
                "summary": "量化技术与动量较强，但资金和情绪偏弱；人工补全后仍需观察销售、现金流及政策催化，不升格为强推荐。",
                "source_status": "HUMAN_REVIEW_COMBINED_WITH_QUANT",
            },
            "observation_rating": "INSUFFICIENT_DATA",
            "requires_manual_review": True,
            "display_marker": "人工补全：基于已核验主营业务、行业分类和财务摘要；概念标签及未知风险保留缺失。",
        })
        screening = dict(original_screening)
        screening["reason"] = "量化技术与动量较强但资金和情绪偏弱；基本面结构化推断失败后已由人工依据核验主营业务、行业分类和财务摘要补全，仍保留风险字段缺失并维持观察。"
        screening["risk_note"] = "房地产行业处于调整期；应收、存货、商誉和股东行为风险仍未知，概念标签缺失。人工补全不构成强推荐依据。"
        screening["requires_manual_review"] = True
        screening["missing_data"] = [
            "concept_tags",
            "financial_status.receivable_risk",
            "financial_status.inventory_risk",
            "financial_status.goodwill_risk",
            "financial_status.shareholder_action_risk",
        ]
        metadata = dict(screening.get("_trader_demo") or {})
        metadata["execution_status"] = "SUCCESS"
        metadata["manual_completed"] = True
        metadata["manual_source"] = "HUMAN_REVIEW_2026-07-22"
        metadata["original_errors"] = original_errors
        metadata["errors"] = []
        screening["_trader_demo"] = metadata
        sample.fundamental_result = fundamental
        sample.screening_result = screening
        session.commit()

        failures = list(session.scalars(select(ModelValidationFailureAudit).where(
            ModelValidationFailureAudit.validation_run_id == args.validation_run,
            ModelValidationFailureAudit.stock_code == args.stock_code,
        )))
        audit = {
            "status": "HUMAN_COMPLETED",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "validation_run_id": args.validation_run,
            "stock_code": args.stock_code,
            "stock_name": sample.stock_name,
            "original_failure_count": len(failures),
            "original_failures": [
                {
                    "task": row.task,
                    "error_category": row.error_category,
                    "error_field": row.error_field,
                    "error_message": row.error_message,
                }
                for row in failures
            ],
            "evidence_scope": ["verified_main_business", "verified_industry", "verified_financial_summary", "quant_scores"],
            "retained_unknowns": screening["missing_data"],
            "manual_decision": screening["screening_decision"],
            "note": "人工补全仅用于当前结果展示和审计，不修改正式Quant评分、Flash Prompt或历史失败审计。",
        }
        args.audit_output.parent.mkdir(parents=True, exist_ok=True)
        args.audit_output.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(audit, ensure_ascii=False, indent=2))
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
