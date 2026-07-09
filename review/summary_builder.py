from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from llm_gateway.service import LLMGatewayService
from review.schemas import (
    ComparisonResult,
    OrderPlanEvaluationResult,
    PortfolioEvaluationResult,
    PredictionEvaluationResult,
)


def build_rule_based_summary(
    prediction_results: list[PredictionEvaluationResult],
    order_results: list[OrderPlanEvaluationResult],
    portfolio_result: PortfolioEvaluationResult,
    comparison_result: ComparisonResult,
    module_scores: dict[str, Decimal],
) -> dict[str, str]:
    correct = len([item for item in prediction_results if item.is_correct is True])
    evaluated = len([item for item in prediction_results if item.is_correct is not None])
    filled = len([item for item in order_results if item.was_filled])
    missed = len([item for item in order_results if item.missed_opportunity])
    risk_avoided = len([item for item in order_results if item.risk_avoided])

    market_summary = (
        "Daily review used deterministic Mock Provider market data only. "
        f"Evaluated predictions={len(prediction_results)}, order plans={len(order_results)}."
    )
    ai_summary = (
        f"AI prediction performance: {correct}/{evaluated} correct. "
        f"Order price quality average={module_scores.get('order_price_quality', Decimal('0'))}. "
        f"Virtual portfolio P/L={portfolio_result.profit_loss} "
        f"({portfolio_result.profit_loss_percent}%), win rate={portfolio_result.win_rate}%. "
        f"Risk control drawdown={portfolio_result.max_drawdown}%."
    )
    mistake_analysis = (
        f"Missed opportunities={missed}; risk avoided cases={risk_avoided}; "
        f"filled order-plan evaluations={filled}. "
        "Review flags are rule-based and should be checked by a human before real trading decisions."
    )
    suggestion = (
        "Tomorrow: focus on plans with low price quality, review wrong prediction directions, "
        "keep real trading disabled, and continue validating via virtual trading."
    )

    return {
        "market_summary": market_summary,
        "ai_summary": ai_summary,
        "human_summary": comparison_result.human_summary,
        "mistake_analysis": mistake_analysis,
        "suggestion": suggestion,
    }


def build_mock_llm_summary(
    rule_summary: dict[str, str],
    module_scores: dict[str, Decimal],
    llm_gateway: LLMGatewayService | None = None,
) -> str:
    gateway = llm_gateway or LLMGatewayService()
    payload: dict[str, Any] = {
        "rule_summary": rule_summary,
        "module_scores": {key: float(value) for key, value in module_scores.items()},
        "safety": {
            "mock_llm_only": True,
            "real_trading_enabled": False,
        },
    }
    response = gateway.chat_simple(
        agent_name="daily_review_agent",
        task="daily_review_summary",
        user_content=json.dumps(payload, ensure_ascii=False, sort_keys=True),
        metadata={"structured": False, "mock_only": True},
    )
    if response.provider != "mock":
        raise RuntimeError("Phase 11 daily review summary must use Mock LLM provider.")
    return response.content
