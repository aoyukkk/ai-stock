from __future__ import annotations

import json

from screening.schemas import LightScreeningInput


SYSTEM_PROMPT = (
    "You are a lightweight A-share screening assistant. "
    "This output is only for AI second-pass screening and is not a real trading instruction. "
    "Do not generate order prices, do not generate real-money trading commands, and do not invent data. "
    "Analyze only the supplied input; never add facts, news, announcements, or market data that were not provided. "
    "Return strict JSON only."
)


def build_light_screening_prompt(inputs: list[LightScreeningInput]) -> str:
    compressed_items = [
        {
            "stock_code": item.stock_code,
            "stock_name": item.stock_name,
            "industry": item.industry,
            "quant_rank": item.quant_rank,
            "quant_total_score": float(item.quant_total_score),
            "technical_score": float(item.technical_score),
            "capital_score": float(item.capital_score),
            "emotion_score": float(item.emotion_score),
            "momentum_score": float(item.momentum_score),
            "risk_score": float(item.risk_score),
            "quant_reason": item.quant_reason[:240],
            "latest_news_summary": item.latest_news_summary,
            "overseas_summary": item.overseas_summary,
            "liquidity_summary": item.liquidity_summary,
            "fundamental_profile_verified": item.fundamental_profile_verified,
            "fundamental_evidence_count": item.fundamental_evidence_count,
            "fundamental_profile": item.fundamental_profile if item.fundamental_profile_verified else None,
            "fundamental_missing_fields": item.fundamental_missing_fields,
        }
        for item in inputs
    ]
    schema = {
        "items": [
            {
                "stock_code": "string",
                "opportunity_score": "0-100",
                "event_catalyst_score": "0-100",
                "sector_strength_score": "0-100",
                "order_friendliness_score": "0-100",
                "liquidity_score": "0-100",
                "risk_penalty_score": "0-100",
                "confidence": "0-1",
                "direction": "BUY|WATCH|NEUTRAL|AVOID",
                "reason": "string",
                "risk_note": "string",
                "should_keep": True,
                "data_conflict": False,
            }
        ]
    }
    return (
        f"{SYSTEM_PROMPT}\n\n"
        "Fundamental data may support an explanation only when fundamental_profile_verified is true. "
        "Unverified or missing fundamental data must never increase a score.\n"
        "Evaluate these compressed stock summaries:\n"
        f"{json.dumps({'stocks': compressed_items}, ensure_ascii=False)}\n\n"
        "Return strict JSON matching this schema:\n"
        f"{json.dumps(schema, ensure_ascii=False)}"
    )
