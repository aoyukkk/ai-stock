from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select

from backend.core.config import get_app_config
from database.models import PostCloseActionResult, PostCloseActionRun
from llm_gateway import LLMGatewayService, LLMMessage, LLMRequest
from post_close.service import PostCloseActionService


RESPONSE_SCHEMA = {
    "type": "object",
    "required": ["stock_code", "review_action", "reviewed_action", "confidence", "reason", "key_risks", "data_conflict"],
    "properties": {
        "stock_code": {"type": "string"},
        "review_action": {"enum": ["CONFIRM", "MORE_CONSERVATIVE", "MANUAL_REVIEW"]},
        "reviewed_action": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
        "key_risks": {"type": "array", "items": {"type": "string"}},
        "data_conflict": {"type": "boolean"},
    },
}


class PostCloseProReviewService:
    def __init__(self, session, gateway: LLMGatewayService | None = None) -> None:
        self.session = session
        config = get_app_config().config_files.get("post_close_action", {}).get("post_close_action", {})
        self.config = config.get("pro_review", {})
        self.gateway = gateway or LLMGatewayService(db_session=session)

    def run(self, run_id: str) -> dict[str, Any]:
        run = self.session.scalar(select(PostCloseActionRun).where(PostCloseActionRun.run_id == run_id))
        if run is None:
            raise ValueError("POST_CLOSE_ACTION_RUN_NOT_FOUND")
        if not bool(self.config.get("enabled", False)):
            run.pro_review_status = "DISABLED"
            self.session.commit()
            return {"run_id": run_id, "status": "DISABLED", "call_count": 0, "fast_result_preserved": True}
        rows = list(self.session.scalars(select(PostCloseActionResult).where(PostCloseActionResult.run_id == run_id)))
        selected = [row for row in rows if row.position_status != "SELECTED_NOT_HELD" or row.baseline_rule_action != row.ifind_shadow_action or row.requires_manual_review]
        selected = selected[:max(1, min(int(self.config.get("max_stocks", 20)), 20))]
        reviews, failures = [], []
        for row in selected:
            try:
                response = self.gateway.chat(LLMRequest(
                    agent_name="post_close_action_review",
                    task="post_close_action_review_v1",
                    task_type="controller",
                    model_alias=str(self.config.get("model_alias", "controller-high-capability")),
                    messages=[
                        LLMMessage(role="system", content="Review the structured advisory result. You may confirm it, make it more conservative, or require manual review. Never create prices, quantities, orders, or execution instructions."),
                        LLMMessage(role="user", content=json.dumps({
                            "stock_code": row.stock_code,
                            "position_status": row.position_status,
                            "baseline_rule_action": row.baseline_rule_action,
                            "ifind_shadow_action": row.ifind_shadow_action,
                            "hard_gate_status": row.hard_gate_status,
                            "data_quality_status": row.data_quality_status,
                            "key_risks": row.key_risks_json,
                        }, ensure_ascii=True, separators=(",", ":"))),
                    ],
                    max_tokens=int(self.config.get("max_tokens", 8000)),
                    response_schema=RESPONSE_SCHEMA,
                    json_mode=True,
                    thinking_mode="disabled",
                    prompt_version=str(self.config.get("prompt_version", "post_close_action_review_v1")),
                    metadata={"run_id": run_id, "stock_code": row.stock_code, "advisory_only": True},
                ))
                payload = response.structured_output or response.parsed_json or json.loads(response.content)
                reviews.append(payload)
            except Exception as exc:
                failures.append({"stock_code": row.stock_code, "error_category": type(exc).__name__})
        applied = PostCloseActionService(self.session).apply_pro_reviews(run_id, reviews) if reviews else {"changed_count": 0}
        run.pro_review_status = "PRO_REVIEWED" if not failures else "PARTIAL_PRO_FAILURE"
        self.session.commit()
        return {"run_id": run_id, "status": run.pro_review_status, "call_count": len(reviews) + len(failures), "reviewed_count": len(reviews), "failure_count": len(failures), "changed_count": applied.get("changed_count", 0), "failures": failures, "fast_result_preserved": True}
