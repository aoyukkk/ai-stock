from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from backend.main import create_app
from database.models.review import DailyReview
from database.session import get_session, init_db
from review.persistence import ensure_daily_review_schema


client = TestClient(create_app())


def assert_no_sensitive(payload: dict) -> None:
    text = json.dumps(payload, ensure_ascii=False).lower()
    for forbidden in ("api_key", "password", "secret", "token", "username"):
        assert forbidden not in text


def memory_payload(title: str = "API memory note") -> dict:
    return {
        "agent_name": "technical_agent",
        "stock_code": "000001",
        "memory_type": "short_term",
        "layer": "stock",
        "title": title,
        "content": "API-created mock memory for Phase 12.",
        "summary": "api memory",
        "importance": "80",
        "confidence": "75",
        "quality_score": "85",
        "source_type": "api_test",
    }


def test_memory_note_search_disable_conflict_and_link_apis() -> None:
    first = client.post("/api/v1/memory/notes", json=memory_payload("API breakout note"))
    second = client.post("/api/v1/memory/notes", json=memory_payload("API breakout sibling"))

    assert first.status_code == 200
    assert second.status_code == 200
    first_payload = first.json()
    second_payload = second.json()
    note_id = first_payload["data"]["id"]
    sibling_id = second_payload["data"]["id"]

    get_response = client.get(f"/api/v1/memory/notes/{note_id}")
    search_response = client.post("/api/v1/memory/search", json={"stock_code": "000001", "keyword": "breakout"})
    link_response = client.post(
        "/api/v1/memory/links",
        json={
            "source_memory_id": note_id,
            "target_memory_id": sibling_id,
            "relation_type": "RELATED",
            "strength": "0.8",
            "reason": "api test",
        },
    )
    disable_response = client.post(f"/api/v1/memory/notes/{note_id}/disable", json={"reason": "api disable"})
    conflict_response = client.post(
        f"/api/v1/memory/notes/{sibling_id}/conflict",
        json={"status": "CONFLICTED", "reason": "api conflict"},
    )

    for response in (get_response, search_response, link_response, disable_response, conflict_response):
        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["data"]["real_trading_enabled"] is False
        assert_no_sensitive(payload)

    assert search_response.json()["data"]["retrieval_log_id"] is not None
    assert disable_response.json()["data"]["should_reuse"] is False
    assert conflict_response.json()["data"]["conflict_status"] == "CONFLICTED"


def test_memory_reflection_playbook_and_config_apis_are_safe() -> None:
    init_db()
    session = get_session()
    try:
        ensure_daily_review_schema(session)
        review = DailyReview(
            date=date(2026, 1, 5),
            market_summary="mock market",
            ai_summary="mock ai",
            mistake_analysis="API review mistake",
            suggestion="API review suggestion",
            prediction_accuracy=Decimal("80"),
            order_price_quality=Decimal("70"),
            profit_loss=Decimal("100.00"),
            max_drawdown=Decimal("2"),
            win_rate=Decimal("60"),
            final_review_score=Decimal("75"),
            module_scores={"prediction_accuracy": 80},
        )
        session.add(review)
        session.commit()
        review_id = review.id
    finally:
        session.close()

    reflection = client.post(f"/api/v1/memory/reflection/from-review/{review_id}")
    playbook = client.post(f"/api/v1/memory/playbook/from-review/{review_id}")
    playbooks = client.get("/api/v1/memory/playbooks")
    config = client.get("/api/v1/memory/config")

    for response in (reflection, playbook, playbooks, config):
        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["data"]["real_trading_enabled"] is False
        assert_no_sensitive(payload)

    config_data = config.json()["data"]
    assert config_data["vector"]["enabled"] is False
    assert config_data["graph"]["enabled"] is False
    assert config_data["external_memory_backends_connected"] is False
