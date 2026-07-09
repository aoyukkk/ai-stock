from agents.base import BaseAgent


def test_agent_parser_parses_structured_json() -> None:
    output = BaseAgent().parse_output(
        """
        {
          "stock_code": "000001",
          "score": 88,
          "direction": "WATCH",
          "confidence": 0.8,
          "reason": "ok",
          "risk_note": "none",
          "action": "ALLOW"
        }
        """
    )

    assert output.stock_code == "000001"
    assert output.score == 88
    assert output.direction == "WATCH"
    assert output.action == "ALLOW"


def test_agent_parser_clamps_scores_and_normalizes_invalid_enums() -> None:
    output = BaseAgent().parse_output(
        """
        {
          "stock_code": "000001",
          "score": 999,
          "direction": "UNKNOWN",
          "confidence": 3,
          "reason": "ok",
          "risk_note": "none",
          "action": "CANCEL_ORDER"
        }
        """
    )

    assert output.score == 100
    assert output.confidence == 1
    assert output.direction == "NEUTRAL"
    assert output.action == "NEED_RECHECK"
