from __future__ import annotations

import json

from screening.schemas import RealLightScreeningInput


PROMPT_VERSION = "light_screening_v0_4"

SYSTEM_PROMPT = (
    "你是 A 股短线交易辅助系统中的轻量二筛模块。"
    "你只能分析输入的结构化量化数据和已提供的信息，不得补充未提供的行情、公告、新闻、财务数据或市场事实。"
    "你没有直接联网搜索权限，不得声称已经搜索网络。"
    "输入缺失、冲突、过期或质量不足时必须降低 confidence，并输出 WATCH_ONLY 或 REJECT。"
    "你的任务不是给出最终买卖建议，只判断是否值得进入下一轮深度分析。"
    "不得输出 BUY/SELL，不得生成挂单价格，不得替代风险 Agent，不得给出实盘下单指令。"
    "必须只输出 JSON。"
)

OUTPUT_SCHEMA = {
    "type": "object",
    "required": [
        "stock_code", "quant_rank", "screening_decision", "llm_score",
        "short_term_opportunity", "factor_consistency", "capital_confirmation",
        "emotion_confirmation", "risk_score", "data_quality_score", "confidence",
        "reason", "risk_note", "data_conflict", "missing_data", "evidence_fields",
    ],
    "properties": {
        "stock_code": {"type": "string"},
        "quant_rank": {"type": "integer"},
        "screening_decision": {"type": "string", "enum": ["ADVANCE", "HOLD", "REJECT", "WATCH_ONLY"]},
        "llm_score": {"type": "number", "minimum": 0, "maximum": 100},
        "short_term_opportunity": {"type": "number", "minimum": 0, "maximum": 100},
        "factor_consistency": {"type": "number", "minimum": 0, "maximum": 100},
        "capital_confirmation": {"type": "number", "minimum": 0, "maximum": 100},
        "emotion_confirmation": {"type": "number", "minimum": 0, "maximum": 100},
        "risk_score": {"type": "number", "minimum": 0, "maximum": 100},
        "data_quality_score": {"type": "number", "minimum": 0, "maximum": 100},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
        "risk_note": {"type": "string"},
        "data_conflict": {"type": "boolean"},
        "missing_data": {"type": "array", "items": {"type": "string"}},
        "evidence_fields": {"type": "array", "items": {"type": "string"}},
        "verified_field_count": {"type": "integer", "minimum": 0},
        "derived_field_count": {"type": "integer", "minimum": 0},
        "unverified_field_count": {"type": "integer", "minimum": 0},
        "unknown_field_count": {"type": "integer", "minimum": 0},
        "research_degradation_level": {"type": "string"},
        "fundamental_observation_rating": {"type": "string"},
        "requires_manual_review": {"type": "boolean"},
    },
}


def build_real_screening_prompt(item: RealLightScreeningInput) -> str:
    return (
        "Only URL-evidence-backed fundamental_profile fields marked verified may be considered; "
        "unverified or missing fundamental information must never increase llm_score.\n"
        "LLM_UNVERIFIED fields may only explain uncertainty or reduce confidence and cannot alone produce ADVANCE.\n"
        "仅分析下面提供的单只股票结构化输入。evidence_fields 只能引用输入字段名。\n"
        + json.dumps(item.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
        + "\n严格按照此 JSON Schema 输出：\n"
        + json.dumps(OUTPUT_SCHEMA, ensure_ascii=False, separators=(",", ":"))
    )
