from __future__ import annotations

import json
import time
from decimal import Decimal

from llm_gateway.base import BaseLLMProvider
from llm_gateway.cost_tracker import estimate_cost_usd
from llm_gateway.schemas import LLMProviderInfo, LLMRequest, LLMResponse
from llm_gateway.token_counter import estimate_messages_tokens, estimate_text_tokens


COMMITTEE_TASKS = {
    "committee_technical",
    "committee_news",
    "committee_capital",
    "committee_emotion",
    "committee_overseas",
    "committee_risk",
    "committee_controller",
}


class MockLLMProvider(BaseLLMProvider):
    def __init__(self, enabled: bool = True) -> None:
        super().__init__(name="mock", enabled=enabled, is_mock=True)

    def list_models(self) -> list[str]:
        return ["mock-chat", "mock-fast", "mock-reasoning"]

    def health_check(self) -> LLMProviderInfo:
        return LLMProviderInfo(
            name=self.name,
            enabled=self.enabled,
            is_mock=True,
            models=self.list_models(),
            status="ok" if self.enabled else "disabled",
            message="Mock LLM provider uses deterministic local responses.",
        )

    def chat(self, request: LLMRequest) -> LLMResponse:
        start = time.perf_counter()
        model = request.model or "mock-chat"
        structured_output = _structured_output_for_task(request)
        content = _content_for_task(request, structured_output)
        input_tokens = estimate_messages_tokens(request.messages)
        output_tokens = estimate_text_tokens(content)
        latency_ms = int((time.perf_counter() - start) * 1000)

        return LLMResponse(
            provider=self.name,
            model=model,
            content=content,
            parsed_json=structured_output,
            structured_output=structured_output,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            cached=False,
            cost_usd=estimate_cost_usd(self.name, model, input_tokens, output_tokens),
            latency_ms=latency_ms,
            request_hash="",
            prompt_version=request.prompt_version,
            model_version=model,
            status="ok",
        )


def _structured_output_for_task(request: LLMRequest) -> dict | None:
    task = request.task.lower()
    if task == "real_light_screening_sample":
        item = request.metadata.get("real_screening_input", {})
        quant_score = float(item.get("quant_score", 50)) if isinstance(item, dict) else 50.0
        risk_score = float(item.get("risk_score", 50)) if isinstance(item, dict) else 50.0
        missing = list(item.get("known_missing_fields", [])) if isinstance(item, dict) else []
        decision = "ADVANCE" if quant_score >= 75 and risk_score >= 50 and not missing else "HOLD"
        if missing:
            decision = "WATCH_ONLY"
        return {
            "stock_code": str(item.get("stock_code", "mock")),
            "quant_rank": int(item.get("quant_rank", 0)),
            "screening_decision": decision,
            "llm_score": max(0, min(100, quant_score)),
            "short_term_opportunity": max(0, min(100, quant_score)),
            "factor_consistency": 75,
            "capital_confirmation": float(item.get("capital_score", 50)),
            "emotion_confirmation": float(item.get("emotion_score", 50)),
            "risk_score": max(0, min(100, risk_score)),
            "data_quality_score": 60 if missing else 85,
            "confidence": 0.55 if missing else 0.8,
            "reason": "Mock structured screening based only on supplied quant fields.",
            "risk_note": "Manual review required; this is not a trading recommendation.",
            "data_conflict": False,
            "missing_data": missing,
            "evidence_fields": ["quant_score", "capital_score", "emotion_score", "risk_score", "known_missing_fields"],
        }
    if task == "connectivity_test":
        return {"gateway_check": "OK", "schema_version": 1}
    if task in COMMITTEE_TASKS:
        return _committee_output(request, task)
    if task in {"light_screening", "llm_light_screening"}:
        return _light_screening_output(request)
    if "risk" in task:
        return {
            "risk_level": "LOW",
            "action": "ALLOW",
            "confidence": 0.8,
            "data_conflict": False,
            "mock": True,
        }
    if "screen" in task or "json" in task or request.metadata.get("structured"):
        return {
            "result": "WATCH",
            "score": 70,
            "confidence": 0.75,
            "data_conflict": False,
            "mock": True,
        }
    return None


def _content_for_task(request: LLMRequest, structured_output: dict | None) -> str:
    if request.task.lower() in COMMITTEE_TASKS and structured_output is not None:
        return json.dumps(structured_output, ensure_ascii=False)
    if request.task.lower() in {"light_screening", "llm_light_screening", "real_light_screening_sample"} and structured_output is not None:
        return json.dumps(structured_output, ensure_ascii=False)
    if structured_output is not None:
        return (
            "Mock structured LLM response. No real model was called. "
            f"Task={request.task}; result={structured_output.get('result', structured_output.get('action'))}."
        )
    last_user = next(
        (message.content for message in reversed(request.messages) if message.role == "user"),
        "",
    )
    return (
        "Mock LLM response from local provider. "
        f"Agent={request.agent_name}; task={request.task}; input={last_user[:80]}"
    )


def _light_screening_output(request: LLMRequest) -> dict:
    items = request.metadata.get("screening_inputs")
    if not isinstance(items, list):
        items = _extract_items_from_prompt(request)

    outputs = []
    for index, item in enumerate(items):
        stock_code = str(item.get("stock_code", f"mock-{index}"))
        quant_score = Decimal(str(item.get("quant_total_score", 50)))
        opportunity = _clamp(quant_score + Decimal(index % 5))
        event = _clamp(quant_score * Decimal("0.80") + Decimal("8"))
        sector = _clamp(Decimal(str(item.get("emotion_score", quant_score))))
        order_friendliness = _clamp(
            (Decimal(str(item.get("technical_score", quant_score))) + Decimal(str(item.get("risk_score", 50)))) / Decimal("2")
        )
        liquidity = _clamp(Decimal(str(item.get("capital_score", quant_score))))
        risk_score = Decimal(str(item.get("risk_score", 50)))
        risk_penalty = _clamp(Decimal("50") - (risk_score - Decimal("50")) / Decimal("2"))
        confidence = max(Decimal("0.50"), min(Decimal("0.95"), quant_score / Decimal("100")))
        direction = "WATCH"
        if quant_score >= 85 and risk_penalty <= 45:
            direction = "BUY"
        elif quant_score < 45:
            direction = "AVOID"
        elif quant_score < 68:
            direction = "NEUTRAL"
        outputs.append(
            {
                "stock_code": stock_code,
                "opportunity_score": float(opportunity),
                "event_catalyst_score": float(event),
                "sector_strength_score": float(sector),
                "order_friendliness_score": float(order_friendliness),
                "liquidity_score": float(liquidity),
                "risk_penalty_score": float(risk_penalty),
                "confidence": float(confidence.quantize(Decimal("0.0001"))),
                "direction": direction,
                "reason": f"Mock light screening based on compressed quant summary for {stock_code}.",
                "risk_note": "Mock risk note; no real model or real market data was used.",
                "should_keep": quant_score >= 50,
                "data_conflict": False,
            }
        )
    return {"items": outputs}


def _committee_output(request: LLMRequest, task: str) -> dict:
    context = request.metadata.get("committee_context")
    if not isinstance(context, dict):
        context = _extract_committee_context_from_prompt(request)

    stock_code = str(context.get("stock_code", "mock"))
    quant_score = Decimal(str(context.get("quant_total_score", context.get("quant_score", 50))))
    light_score = Decimal(str(context.get("final_light_score", context.get("light_score", quant_score))))
    base_score = (quant_score + light_score) / Decimal("2")
    risk_text = " ".join(
        str(context.get(key, ""))
        for key in ("risk_summary", "light_reason", "news_summary")
    ).lower()

    if task == "committee_controller":
        controller_result = request.metadata.get("controller_result")
        score = Decimal(str(controller_result.get("final_score", base_score))) if isinstance(controller_result, dict) else base_score
        action = "ALLOW"
        if isinstance(controller_result, dict) and controller_result.get("recommendation") == "BLOCKED":
            action = "BLOCK"
        direction = _direction_from_score(score)
        return {
            "stock_code": stock_code,
            "score": float(_clamp(score)),
            "direction": direction,
            "confidence": float(_confidence_from_score(score)),
            "reason": f"Mock controller summary for {stock_code}; final score is rule-based and auditable.",
            "risk_note": "Mock controller risk summary; no order prices or trading execution commands were generated.",
            "action": action,
            "data_conflict": False,
        }

    score_adjustments = {
        "committee_technical": Decimal("35"),
        "committee_news": Decimal("32"),
        "committee_capital": Decimal("31"),
        "committee_emotion": Decimal("33"),
        "committee_overseas": Decimal("29"),
        "committee_risk": Decimal("37"),
    }
    score = _clamp(base_score + score_adjustments.get(task, Decimal("0")))
    action = "ALLOW"
    if task == "committee_risk":
        if any(marker in risk_text for marker in ("black_swan", "black swan", "block", "suspend", "重大风险")) or light_score < 25:
            score = Decimal("30.0000")
            action = "BLOCK"
        elif light_score < 45:
            action = "WATCH_ONLY"
        else:
            action = "ALLOW"

    return {
        "stock_code": stock_code,
        "score": float(score),
        "direction": _direction_from_score(score),
        "confidence": float(_confidence_from_score(base_score)),
        "reason": f"Mock {task} analysis based on compressed committee context for {stock_code}.",
        "risk_note": "Mock committee risk note; no real model or real market data was used.",
        "action": action,
        "data_conflict": False,
    }


def _extract_committee_context_from_prompt(request: LLMRequest) -> dict:
    user_text = next(
        (message.content for message in reversed(request.messages) if message.role == "user"),
        "",
    )
    start = user_text.find('{"stock_code":')
    if start == -1:
        start = user_text.find('"stock":')
        if start == -1:
            return {}
        brace = user_text.rfind("{", 0, start)
        start = brace if brace != -1 else start
    end = user_text.rfind("}")
    if end == -1 or end <= start:
        return {}
    try:
        parsed = json.loads(user_text[start : end + 1])
    except json.JSONDecodeError:
        return {}
    if isinstance(parsed, dict) and isinstance(parsed.get("stock"), dict):
        return parsed["stock"]
    return parsed if isinstance(parsed, dict) else {}


def _direction_from_score(score: Decimal) -> str:
    if score >= 85:
        return "BUY"
    if score >= 65:
        return "WATCH"
    if score >= 45:
        return "NEUTRAL"
    return "AVOID"


def _confidence_from_score(score: Decimal) -> Decimal:
    return max(Decimal("0.50"), min(Decimal("0.95"), (score / Decimal("100")) + Decimal("0.15"))).quantize(Decimal("0.0001"))


def _extract_items_from_prompt(request: LLMRequest) -> list[dict]:
    user_text = next(
        (message.content for message in reversed(request.messages) if message.role == "user"),
        "",
    )
    marker = '{"stocks":'
    start = user_text.find(marker)
    if start == -1:
        return []
    end = user_text.find("\n\nReturn strict JSON", start)
    if end == -1:
        end = len(user_text)
    try:
        parsed = json.loads(user_text[start:end])
    except json.JSONDecodeError:
        return []
    return parsed.get("stocks", []) if isinstance(parsed, dict) else []


def _clamp(value: Decimal) -> Decimal:
    return max(Decimal("0"), min(Decimal("100"), value)).quantize(Decimal("0.0001"))
