from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import select

from backend.application.excel_export import write_tabular_workbook
from database.models import PostCloseActionResult, PostCloseActionRun
from post_close.service import PostCloseActionService, _result_dict


ACTION_LABELS = {
    "CONTINUE_HOLD": "继续持有",
    "HOLD_WITH_TIGHT_STOP": "继续持有并收紧止损",
    "REDUCE_POSITION": "建议减仓",
    "EXIT_NEXT_SESSION": "次日退出准备",
    "EXIT_WHEN_TRADABLE": "可交易时退出",
    "T_PLUS_ONE_LOCKED_EXIT_PLAN": "今日T+1锁定，准备下一交易日退出",
    "MANUAL_REVIEW": "人工复核",
    "DATA_INSUFFICIENT": "数据不足",
    "PREPARE_ENTRY": "准备次日计划",
    "KEEP_WATCH": "继续观察",
    "DO_NOT_CHASE": "不宜追高",
    "REMOVE_FROM_POOL": "移出候选",
}


class PostCloseActionExcelService:
    def __init__(self, session, output_root: Path) -> None:
        self.session = session
        self.output_root = output_root

    def export(self, run_id: str) -> dict[str, Any]:
        run = self.session.scalar(select(PostCloseActionRun).where(PostCloseActionRun.run_id == run_id))
        if run is None:
            raise ValueError("POST_CLOSE_ACTION_RUN_NOT_FOUND")
        rows = list(self.session.scalars(select(PostCloseActionResult).where(PostCloseActionResult.run_id == run_id).order_by(PostCloseActionResult.stock_code)))
        held = [_excel_row(row, run) for row in rows if _is_held_position(row.position_status)]
        candidates = [_excel_row(row, run) for row in rows if _is_confirmed_nonheld(row.position_status)]
        compare = [{"股票代码": row.stock_code, "原方案规则建议": ACTION_LABELS.get(row.baseline_rule_action, row.baseline_rule_action), "iFinD影子建议": ACTION_LABELS.get(row.ifind_shadow_action, row.ifind_shadow_action), "当前采用建议": ACTION_LABELS.get(row.current_adopted_action, row.current_adopted_action), "是否一致": "是" if row.baseline_rule_action == row.ifind_shadow_action else "否"} for row in rows]
        fast_final = PostCloseActionService(self.session).compare_fast_final(run.trade_date)
        fast_final_rows = [{"股票代码": item["stock_code"], "Fast建议": ACTION_LABELS.get(item["fast_action"], item["fast_action"]), "Final建议": ACTION_LABELS.get(item["final_action"], item["final_action"]), "是否变化": "是" if item["action_changed"] else "否", "变化方向": item["change_direction"], "变化原因": item["change_reason"], "Fast数据层级": item["fast_data_scope"], "Final数据层级": item["final_data_scope"], "人工复核": "是" if item["requires_manual_review"] else "否"} for item in fast_final.get("items", [])]
        exceptions = [{"股票代码": row.stock_code, "Hard Gate": row.hard_gate_status, "数据质量": row.data_quality_status, "需要人工复核": "是" if row.requires_manual_review else "否", "主要风险": "\n".join(row.key_risks_json)} for row in rows if row.requires_manual_review or row.hard_gate_status != "PASS" or row.data_quality_status not in {"PASS", "BASELINE_ONLY"}]
        output = self.output_root / run.trade_date.isoformat() / f"盘后操作建议_{run.trade_date.isoformat()}_{run.run_id[-8:]}.xlsx"
        result = write_tabular_workbook(output, {"01_持仓操作建议": held, "02_未持仓候选建议": candidates, "03_Fast与Final对比": fast_final_rows, "04_Base与iFinD影子对比": compare, "05_规则与异常": exceptions})
        return {**result, "run_id": run_id, "held_rows": len(held), "candidate_rows": len(candidates), "comparison_rows": len(compare), "exception_rows": len(exceptions)}


def daily_sheet_rows(session, trade_date) -> list[dict[str, Any]]:
    run = session.scalar(select(PostCloseActionRun).where(PostCloseActionRun.trade_date == trade_date, PostCloseActionRun.status == "SUCCESS").order_by(PostCloseActionRun.created_at.desc()))
    if run is None:
        return [{"状态": "尚无盘后操作建议", "交易日": trade_date, "真实交易": "关闭"}]
    return [_excel_row(row, run) for row in session.scalars(select(PostCloseActionResult).where(PostCloseActionResult.run_id == run.run_id).order_by(PostCloseActionResult.stock_code))]


def _excel_row(row: PostCloseActionResult, run: PostCloseActionRun | None = None) -> dict[str, Any]:
    data = _result_dict(row)
    coverage = run.config_snapshot_json.get("tiered_coverage", {}) if run else {}
    truth_status = "CONFIRMED_POSITIONS" if _is_held_position(row.position_status) else "CONFIRMED_EMPTY" if row.position_status == "SELECTED_NOT_HELD" else "MISSING"
    return {
        "持仓快照状态": truth_status, "Fast/Final版本": run.run_mode if run else None,
        "数据覆盖层级": coverage.get("feature_scope"), "Partial数量": coverage.get("partial_only_count"), "Full数量": coverage.get("full_overlay_count"),
        "股票代码": row.stock_code, "股票名称": row.stock_name, "是否持仓": "是" if row.position_status in {"HUMAN_HELD", "AI_SIMULATION_HELD", "BOTH_HELD"} else "持仓数据缺失" if row.position_status == "POSITION_DATA_MISSING" else "否",
        "持仓账户类型": row.account_scope, "选择来源": row.selection_source, "当前数量": row.quantity,
        "当前可卖数量": row.available_quantity, "次日可卖数量": row.target_day_sellable_quantity,
        "成本价": data["cost_price"], "收盘价": data["close_price"], "浮盈亏": data["unrealized_return"], "持有天数": row.holding_days,
        "Base Quant分": data["base_score"], "Base排名": row.base_rank, "iFinD增强分": data["enhanced_shadow_score"],
        "Enhanced排名": row.enhanced_rank, "排名变化": row.base_rank - row.enhanced_rank if row.base_rank and row.enhanced_rank else None,
        "数据质量": row.data_quality_status, "Hard Gate": row.hard_gate_status,
        "原方案规则建议": ACTION_LABELS.get(row.baseline_rule_action, row.baseline_rule_action),
        "iFinD影子建议": ACTION_LABELS.get(row.ifind_shadow_action, row.ifind_shadow_action),
        "Fast建议": ACTION_LABELS.get(row.current_adopted_action, row.current_adopted_action) if run and run.run_mode == "POST_CLOSE_FAST" else None,
        "Final建议": ACTION_LABELS.get(row.current_adopted_action, row.current_adopted_action) if run and run.run_mode == "POST_CLOSE_FINAL" else None,
        "Pro复核": ACTION_LABELS.get(row.pro_review_action, row.pro_review_action),
        "当前采用建议": ACTION_LABELS.get(row.current_adopted_action, row.current_adopted_action),
        "当前仓位": data["current_position_percent"], "建议目标仓位": data["suggested_target_position_percent"],
        "建议减仓比例": data["suggested_reduce_percent"], "建议减仓股数": row.suggested_reduce_quantity,
        "止损价": data["stop_loss_price"], "止盈1": data["take_profit_1"], "止盈2": data["take_profit_2"],
        "主要依据": "\n".join(row.key_reasons_json), "主要风险": "\n".join(row.key_risks_json),
        "是否需要人工复核": "是" if row.requires_manual_review else "否", "建议版本": row.advice_version,
    }


def _is_held_position(position_status: str) -> bool:
    return position_status in {"HUMAN_HELD", "AI_SIMULATION_HELD", "BOTH_HELD"}


def _is_confirmed_nonheld(position_status: str) -> bool:
    return position_status == "SELECTED_NOT_HELD"
