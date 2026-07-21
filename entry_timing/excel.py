from __future__ import annotations

from datetime import date

from sqlalchemy import select

from database.models.entry_timing import AdmissionRun, EntryTimingResult
from database.models.entry_timing_v2 import AdmissionV2Run, EntryTimingV2Result
from stock_codes import display_stock_code


def latest_entry_timing_rows(session, trade_date: date) -> tuple[str | None, list[dict]]:
    v2_run = session.scalar(
        select(AdmissionV2Run)
        .where(AdmissionV2Run.trade_date == trade_date)
        .order_by(AdmissionV2Run.created_at.desc())
    )
    if v2_run is not None:
        v2_results = list(
            session.scalars(
                select(EntryTimingV2Result)
                .where(EntryTimingV2Result.run_id == v2_run.run_id)
                .order_by(EntryTimingV2Result.pool_type, EntryTimingV2Result.quant_rank)
            )
        )
        return v2_run.run_id, [entry_timing_v2_excel_row(row) for row in v2_results]
    run = session.scalar(
        select(AdmissionRun)
        .where(AdmissionRun.trade_date == trade_date)
        .order_by(AdmissionRun.created_at.desc())
    )
    if run is None:
        return None, []
    results = list(
        session.scalars(
            select(EntryTimingResult)
            .where(EntryTimingResult.admission_run_id == run.run_id)
            .order_by(EntryTimingResult.pool_type, EntryTimingResult.quant_rank)
        )
    )
    return run.run_id, [entry_timing_excel_row(row) for row in results]


def entry_timing_excel_row(row: EntryTimingResult) -> dict:
    return {
        "股票代码": display_stock_code(row.stock_code),
        "股票名称": row.stock_name,
        "候选池": "模型候选池" if row.pool_type == "AI_POOL" else "人工挑战池",
        "Quant排名": row.quant_rank,
        "Quant分": float(row.quant_score),
        "Flash分": float(row.flash_score) if row.flash_score is not None else None,
        "时机总分": float(row.entry_timing_score),
        "价格位置(25)": float(row.position_score),
        "回撤质量(20)": float(row.pullback_score),
        "量价结构(20)": float(row.volume_price_score),
        "板块共振(15)": float(row.sector_score),
        "市场适配(10)": float(row.market_score),
        "流动性(10)": float(row.liquidity_score),
        "数据质量": float(row.data_quality_score),
        "准入状态": {"PASS": "通过", "REVIEW": "观察", "BLOCK": "阻断", "DATA_INSUFFICIENT": "数据不足"}.get(row.admission_status, row.admission_status),
        "风险标签": "、".join(row.risk_flags or []) or "无",
        "阻断原因": "、".join(row.block_reasons or []) or "无",
        "人工分": float(row.manual_score) if row.manual_score is not None else None,
        "模型分": float(row.ai_score) if row.ai_score is not None else None,
        "分差": float(row.score_difference) if row.score_difference is not None else None,
        "配置版本": row.config_version,
    }


def entry_timing_v2_excel_row(row: EntryTimingV2Result) -> dict:
    status_text = {"PASS": "通过", "REVIEW": "观察", "BLOCK": "阻断", "DATA_INSUFFICIENT": "数据不足"}
    return {
        "股票代码": display_stock_code(row.stock_code),
        "股票名称": row.stock_name,
        "候选池": "模型候选池" if row.pool_type == "AI_POOL" else "人工挑战池",
        "Quant排名": row.quant_rank,
        "Quant分": float(row.quant_score),
        "风险分": float(row.risk_score) if row.risk_score is not None else None,
        "策略分类": row.strategy_id,
        "策略匹配分": float(row.strategy_fit_score),
        "市场情绪分": float(row.market_emotion_score) if row.market_emotion_score is not None else None,
        "市场情绪": row.market_emotion_state,
        "市场状态": row.market_regime,
        "市场门禁": row.market_gate_status,
        "时机V1分": float(row.entry_timing_v1_score),
        "时机V2分": float(row.entry_timing_v2_score) if row.entry_timing_v2_score is not None else None,
        "准入排序分V2": float(row.admission_ranking_score_v2) if row.admission_ranking_score_v2 is not None else None,
        "准入V1": status_text.get(row.admission_status_v1, row.admission_status_v1),
        "准入V2": status_text.get(row.admission_status_v2, row.admission_status_v2),
        "风险标签": "、".join(row.risk_flags_json or []) or "无",
        "阻断原因": "、".join(row.block_reasons_json or []) or "无",
        "观察原因": "、".join(row.review_reasons_json or []) or "无",
        "配置版本": row.version,
    }
