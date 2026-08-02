from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any

from sqlalchemy import select

from backend.application.excel_export import write_tabular_workbook
from database.models import MiddayRecommendationResult, MiddayRecommendationRun
from midday.human_excel import build_midday_human_payload, write_midday_human_workbook
from midday.service import _result_dict
from reporting.workbook_standard import validate_trading_assistant_workbook


ACTION_LABELS = {
    "AFTERNOON_PREPARE_ENTRY": "午后准备介入",
    "WAIT_PULLBACK": "等待回落",
    "KEEP_WATCH": "继续观察",
    "REMOVE_FROM_POOL": "移出候选池",
    "DO_NOT_CHASE": "不追高",
    "MANUAL_REVIEW": "人工复核",
    "CONTINUE_HOLD": "继续持有",
    "HOLD_WITH_TIGHT_STOP": "持有并收紧止损",
    "REDUCE_IF_WEAKENS": "走弱时减仓",
    "EXIT_IF_TRIGGERED": "触发条件时退出",
    "T_PLUS_ONE_LOCKED": "T+1 锁定",
    "DATA_INSUFFICIENT": "数据不足",
}


class MiddayRecommendationExcelService:
    def __init__(self, session, output_root: Path) -> None:
        self.session = session
        self.output_root = output_root

    def export(self, run_id: str) -> dict[str, Any]:
        run = self.session.scalar(select(MiddayRecommendationRun).where(MiddayRecommendationRun.run_id == run_id))
        if run is None:
            raise ValueError("MIDDAY_RUN_NOT_FOUND")
        rows = list(self.session.scalars(select(MiddayRecommendationResult).where(
            MiddayRecommendationResult.run_id == run_id,
        ).order_by(MiddayRecommendationResult.pro_rank.is_(None), MiddayRecommendationResult.pro_rank, MiddayRecommendationResult.base_quant_rank)))
        final = [_recommendation_row(row) for row in rows if row.pro_rank is not None]
        held = [_recommendation_row(row) for row in rows if row.position_status == "HELD"]
        all_rows = [_score_row(row) for row in rows]
        exceptions = [_exception_row(row) for row in rows if row.requires_manual_review or row.hard_gate_status != "PASS"]
        summary = [{
            "运行编号": run.run_id,
            "决策日期": run.session_trade_date,
            "决策时间": run.decision_time,
            "运行状态": run.status,
            "基线交易日": run.baseline_trade_date,
            "基线量化运行": run.baseline_quant_run_id,
            "候选池数量": run.base_pool_count,
            "快照覆盖": run.snapshot_count,
            "分钟线覆盖": run.minute_count,
            "Flash完成": run.flash_count,
            "Pro完成": run.pro_count,
            "最终推荐": run.final_count,
            "确认持仓": run.held_count,
            "有效开始": run.valid_from,
            "有效截止": run.valid_until,
            "下午复核": "需要" if run.recheck_required else "不需要",
            "真实下单": "关闭",
        }]
        daily_output_dir = self.output_root / run.session_trade_date.isoformat()
        output_dir = daily_output_dir / "午盘"
        history_dir = output_dir / "历史版本"
        audit_dir = output_dir / "审计"
        history_dir.mkdir(parents=True, exist_ok=True)
        audit_dir.mkdir(parents=True, exist_ok=True)
        detail_output = history_dir / f"午间推荐_{run.session_trade_date.isoformat()}_{run.run_id[-8:]}_明细.xlsx"
        detail_result = write_tabular_workbook(detail_output, {
            "01_运行摘要": summary,
            "02_午后推荐": final,
            "03_持仓建议": held,
            "04_全池评分": all_rows,
            "05_异常与复核": exceptions,
        })
        output = output_dir / f"智能交易助手_午盘_{run.session_trade_date.isoformat()}.xlsx"
        candidate_output = output.with_name(f".{output.stem}_{run.run_id[-8:]}_candidate.xlsx")
        payload = build_midday_human_payload(self.session, run, rows)
        preview_dir = output_dir / "预览" / f"午间推荐_{run.run_id[-8:]}"
        try:
            write_midday_human_workbook(payload, candidate_output, preview_dir)
            validation = validate_trading_assistant_workbook(candidate_output)
            candidate_output.replace(output)
        finally:
            candidate_output.unlink(missing_ok=True)
            Path(f"{candidate_output}.inspect.ndjson").unlink(missing_ok=True)
        Path(f"{output}.inspect.ndjson").unlink(missing_ok=True)
        digest = hashlib.sha256(output.read_bytes()).hexdigest()
        run.excel_path = str(output)
        self.session.commit()
        audit = audit_dir / f"{output.stem}_audit.json"
        audit.write_text(json.dumps({
            "run_id": run.run_id, "trade_date": run.session_trade_date.isoformat(),
            "baseline_trade_date": run.baseline_trade_date.isoformat() if run.baseline_trade_date else None,
            "baseline_quant_run_id": run.baseline_quant_run_id, "baseline_manifest_id": run.baseline_manifest_id,
            "workbook_sha256": digest, "sheet_count": validation["sheet_count"],
            "final_count": len(final), "held_count": len(held), "exception_count": len(exceptions),
            "detail_output_path": detail_result["output_path"],
            "workbook_validation": validation,
            "advisory_only": True, "actionable": False, "orders_created": 0,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "output_path": str(output),
            "detail_output_path": detail_result["output_path"],
            "size": output.stat().st_size,
            "sha256": digest,
            "sheet_count": validation["sheet_count"],
            "status": "SUCCESS",
            "audit_path": str(audit),
            "run_id": run_id,
            "final_count": len(final),
            "held_count": len(held),
            "exception_count": len(exceptions),
            "validation": validation,
        }


def _recommendation_row(row: MiddayRecommendationResult) -> dict[str, Any]:
    data = _result_dict(row)
    return {
        "最终排名": row.pro_rank,
        "股票代码": row.stock_code,
        "股票名称": row.stock_name,
        "是否持仓": "是" if row.position_status == "HELD" else "否",
        "入池来源": "、".join(row.pool_sources),
        "基线排名": row.base_quant_rank,
        "基线分": data["base_quant_score"],
        "午间增强分": data["midday_enhanced_score"],
        "Flash分": data["flash_score"],
        "Pro分": data["pro_score"],
        "数据范围": row.feature_scope,
        "候选建议": ACTION_LABELS.get(row.candidate_action, row.candidate_action),
        "持仓建议": ACTION_LABELS.get(row.held_action, row.held_action),
        "参考价": data["recommended_price"],
        "最高接受价": data["max_acceptable_price"],
        "止损价": data["stop_loss"],
        "止盈一": data["take_profit_1"],
        "止盈二": data["take_profit_2"],
        "建议权重": data["suggested_weight"],
        "主要依据": "\n".join(row.key_reasons),
        "主要风险": "\n".join(row.key_risks),
        "有效截止": row.valid_until,
        "需要复核": "是" if row.recheck_required else "否",
    }


def _score_row(row: MiddayRecommendationResult) -> dict[str, Any]:
    data = _result_dict(row)
    return {
        "股票代码": row.stock_code,
        "股票名称": row.stock_name,
        "入池来源": "、".join(row.pool_sources),
        "持仓状态": row.position_status,
        "基线排名": row.base_quant_rank,
        "基线分": data["base_quant_score"],
        "数据范围": row.feature_scope,
        "影子增强分": data["midday_overlay_score"],
        "影子增量": data["midday_delta"],
        "午间增强分": data["midday_enhanced_score"],
        "Flash结论": row.flash_decision,
        "Flash分": data["flash_score"],
        "Pro分": data["pro_score"],
        "最终排名": row.pro_rank,
        "硬门禁": row.hard_gate_status,
        "人工复核": "是" if row.requires_manual_review else "否",
    }


def _exception_row(row: MiddayRecommendationResult) -> dict[str, Any]:
    return {
        "股票代码": row.stock_code,
        "股票名称": row.stock_name,
        "数据范围": row.feature_scope,
        "硬门禁": row.hard_gate_status,
        "主要风险": "\n".join(row.key_risks),
        "处理方式": "人工复核",
    }
