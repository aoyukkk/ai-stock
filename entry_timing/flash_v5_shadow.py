from __future__ import annotations

from typing import Any


FLASH_V5_SHADOW_PROMPT_VERSION = "flash_v5_short_term_shadow_v1"
FLASH_V5_SHADOW_WEIGHTS = {
    "short_term_trend": 0.30,
    "capital_behavior": 0.20,
    "sector_strength": 0.20,
    "catalyst_persistence": 0.15,
    "fundamentals": 0.15,
}

FLASH_V5_SHADOW_PROMPT = """你是短线方向影子复核器，只做研究评分，不给出买卖指令。
输入包含 Quant、Entry Timing、市场状态、价格位置风险和流动性。
按短线趋势30%、资金行为20%、板块强度20%、催化持续性15%、基本面15%评估。
输出 chase_risk、entry_quality、short_term_probability、holding_suitability 和审计理由。
不得改变 Flash V4、Pro V3 或生产推荐。"""


def build_flash_v5_shadow_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "prompt_version": FLASH_V5_SHADOW_PROMPT_VERSION,
        "weights": FLASH_V5_SHADOW_WEIGHTS,
        "entry_timing_score": row.get("entry_timing_score"),
        "market_regime": (row.get("diagnostics") or {}).get("market_regime"),
        "price_position_risk": row.get("risk_flags") or [],
        "liquidity_score": row.get("liquidity_score"),
        "execution_status": "NOT_CALLED_SHADOW_CONTRACT_ONLY",
        "advisory_only": True,
    }
