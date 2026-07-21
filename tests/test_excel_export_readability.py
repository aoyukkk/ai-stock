from scripts.build_three_trade_day_comparison import (
    market_regime_label,
    zero_position_reason_text,
)


def test_market_regime_labels_are_human_readable() -> None:
    assert market_regime_label("BROAD_RALLY") == "普涨反弹"
    assert market_regime_label("BROAD_SELL_OFF") == "普跌调整"
    assert market_regime_label(None) == "数据不足"


def test_zero_position_reasons_are_human_readable() -> None:
    assert zero_position_reason_text(
        {"RISK_GATE_BLOCKED_OR_NO_RECOMMENDED_PRICE": 5}
    ) == "风险规则阻断或缺少有效参考价：5"
    assert zero_position_reason_text({}) == "无"
