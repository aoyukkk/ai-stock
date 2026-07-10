from screening.real_prompt import OUTPUT_SCHEMA, SYSTEM_PROMPT


def test_real_screening_prompt_has_required_safety_boundaries() -> None:
    assert "不得声称已经搜索网络" in SYSTEM_PROMPT
    assert "不得补充" in SYSTEM_PROMPT
    assert "不得输出 BUY/SELL" in SYSTEM_PROMPT
    assert "不得生成挂单价格" in SYSTEM_PROMPT
    assert "WATCH_ONLY" in SYSTEM_PROMPT
    assert OUTPUT_SCHEMA["properties"]["screening_decision"]["enum"] == [
        "ADVANCE", "HOLD", "REJECT", "WATCH_ONLY"
    ]
