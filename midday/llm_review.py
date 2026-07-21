from __future__ import annotations

import json
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from database.models import LLMUsage
from llm_gateway import LLMGatewayService, LLMMessage, LLMRequest


FLASH_SCHEMA = {
    "type": "object",
    "properties": {
        "stock_code": {"type": "string"},
        "score": {"type": "number", "minimum": 0, "maximum": 100},
        "decision": {"type": "string", "enum": ["PASS", "WATCH", "REJECT", "MANUAL_REVIEW"]},
        "reasons": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
        "risks": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
    },
    "required": ["stock_code", "score", "decision", "reasons", "risks"],
    "additionalProperties": False,
}

PRO_SCHEMA = {
    "type": "object",
    "properties": {
        "stock_code": {"type": "string"},
        "score": {"type": "number", "minimum": 0, "maximum": 100},
        "decision": {"type": "string", "enum": ["PRIORITY", "WATCH", "REJECT", "MANUAL_REVIEW"]},
        "reasons": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
        "risks": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
    },
    "required": ["stock_code", "score", "decision", "reasons", "risks"],
    "additionalProperties": False,
}


class MiddayLLMReviewer:
    def __init__(self, session, config: dict[str, Any], *, gateway: LLMGatewayService | None = None) -> None:
        self.session = session
        self.config = config
        self.gateway = gateway or LLMGatewayService(db_session=session)
        self.calls = 0
        self.tokens = 0
        self.cost = 0.0
        self.audits: list[dict[str, Any]] = []

    def gate(self) -> dict[str, Any]:
        return {
            "passed": not bool(self.gateway.config.mock_only),
            "status": "READY" if not self.gateway.config.mock_only else "BLOCKED_MOCK_ONLY",
        }

    def flash(self, item: dict[str, Any], run_id: str) -> dict[str, Any]:
        return self._review(item, run_id, stage="flash", schema=FLASH_SCHEMA)

    def pro(self, item: dict[str, Any], run_id: str) -> dict[str, Any]:
        return self._review(_compact_pro_input(item), run_id, stage="pro", schema=PRO_SCHEMA)

    def usage(self) -> dict[str, Any]:
        return {"calls": self.calls, "tokens": self.tokens, "cost": round(self.cost, 8)}

    def review_many(self, stage: str, items: list[tuple[str, dict[str, Any]]], run_id: str, *, concurrency: int = 3) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
        results: dict[str, dict[str, Any]] = {}
        failures: list[dict[str, str]] = []

        def work(code: str, payload: dict[str, Any]):
            gateway = LLMGatewayService(registry=self.gateway.registry, db_session=None)
            local = MiddayLLMReviewer(None, self.config, gateway=gateway)
            value = local.flash(payload, run_id) if stage == "FLASH" else local.pro(payload, run_id)
            return code, value, local.usage(), local.audits

        with ThreadPoolExecutor(max_workers=max(1, min(concurrency, 3))) as executor:
            futures = {executor.submit(work, code, payload): code for code, payload in items}
            for future in as_completed(futures):
                code = futures[future]
                try:
                    _, value, usage, audits = future.result()
                    results[code] = value
                    self.calls += int(usage["calls"])
                    self.tokens += int(usage["tokens"])
                    self.cost += float(usage["cost"])
                    self.audits.extend(audits)
                except Exception as exc:
                    failures.append({"stock_code": code, "stage": f"{stage}_RETRY", "error_category": type(exc).__name__})
        self._persist_audits(run_id)
        return results, failures

    def _review(self, item: dict[str, Any], run_id: str, *, stage: str, schema: dict[str, Any]) -> dict[str, Any]:
        if not self.gate()["passed"]:
            raise RuntimeError("REAL_LLM_REQUIRED")
        section = self.config.get("llm", {})
        system = "Return JSON only and match the response schema. Review only the supplied structured facts for afternoon advisory screening. Do not invent prices, quantities, orders, execution instructions, hidden facts, or reasoning traces."
        if stage == "pro":
            system = 'Return exactly one compact JSON object and no other text: {"stock_code":"...","score":0,"decision":"PRIORITY|WATCH|REJECT|MANUAL_REVIEW","reasons":[],"risks":[]}. Use only supplied facts. Never create prices, quantities, orders, execution instructions, or reasoning traces.'
        response = self.gateway.chat(LLMRequest(
            agent_name=f"midday_{stage}_review",
            task=f"midday_{stage}_review_v1",
            task_type="screening" if stage == "flash" else "controller",
            model_alias=str(section.get(f"{stage}_model_alias", f"deepseek-v4-{stage}")),
            messages=[
                LLMMessage(role="system", content=system),
                LLMMessage(role="user", content=json.dumps(item, ensure_ascii=True, separators=(",", ":"), default=str)),
            ],
            max_tokens=int(section.get("pro_retry_max_tokens", 700) if stage == "pro" else section.get("flash_max_tokens", 1400)),
            prompt_version=str(section.get(f"{stage}_prompt_version", f"midday_{stage}_v1")),
            response_schema=schema,
            json_mode=True,
            allow_fallback=False,
            thinking_mode="disabled",
            metadata={"run_id": run_id, "stock_code": item.get("stock_code"), "advisory_only": True},
        ))
        if response.status != "ok":
            raise RuntimeError(f"LLM_{response.status.upper()}")
        payload = response.structured_output or response.parsed_json or json.loads(response.content)
        if payload.get("stock_code") != item.get("stock_code"):
            raise ValueError("LLM_STOCK_CODE_MISMATCH")
        self.calls += 1
        self.tokens += int(response.total_tokens)
        self.cost += float(response.cost_usd or 0.0)
        self.audits.append({
            "provider": response.provider, "model_name": response.model, "model_alias": response.model_alias,
            "agent_name": f"midday_{stage}_review", "task": f"midday_{stage}_review_v1",
            "task_type": response.task_type, "task_tier": response.task_tier,
            "thinking_mode": response.thinking_mode, "prompt_version": response.prompt_version,
            "input_tokens": response.input_tokens, "input_cache_hit_tokens": response.input_cache_hit_tokens,
            "input_cache_miss_tokens": response.input_cache_miss_tokens, "output_tokens": response.output_tokens,
            "total_tokens": response.total_tokens, "cost_usd": response.cost_usd,
            "cost_status": response.cost_status, "pricing_version": response.pricing_version,
            "latency_ms": response.latency_ms, "status": response.status, "finish_reason": response.finish_reason,
            "request_hash": response.request_hash,
        })
        return payload

    def _persist_audits(self, run_id: str) -> None:
        if self.session is None or not self.audits:
            return
        for index, audit in enumerate(self.audits):
            self.session.add(LLMUsage(
                provider=audit["provider"], model_name=audit["model_name"], model_alias=audit["model_alias"],
                agent_name=audit["agent_name"], task=audit["task"], task_type=audit["task_type"], task_tier=audit["task_tier"],
                thinking_mode=audit["thinking_mode"], prompt_version=audit["prompt_version"],
                input_tokens=audit["input_tokens"], input_cache_hit_tokens=audit["input_cache_hit_tokens"],
                input_cache_miss_tokens=audit["input_cache_miss_tokens"], output_tokens=audit["output_tokens"],
                total_tokens=audit["total_tokens"], cost_usd=audit["cost_usd"], cost_status=audit["cost_status"],
                pricing_version=audit["pricing_version"], latency_ms=audit["latency_ms"], status=audit["status"],
                finish_reason=audit["finish_reason"], request_hash=audit["request_hash"], pipeline_run_id=run_id,
                usage_source="REAL_API", call_id=f"{run_id}:parallel:{index}:{uuid.uuid4().hex[:12]}",
            ))
        self.session.commit()
        self.audits.clear()


def _compact_pro_input(item: dict[str, Any]) -> dict[str, Any]:
    flash = item.get("flash") if isinstance(item.get("flash"), dict) else {}
    return {
        "stock_code": item.get("stock_code"),
        "position_status": item.get("position_status"),
        "base_quant_rank": item.get("base_quant_rank"),
        "base_quant_score": item.get("base_quant_score"),
        "feature_scope": item.get("feature_scope"),
        "midday_enhanced_score": item.get("midday_enhanced_score"),
        "hard_gate_status": item.get("hard_gate_status"),
        "flash_score": flash.get("score"),
        "flash_decision": flash.get("decision"),
    }
