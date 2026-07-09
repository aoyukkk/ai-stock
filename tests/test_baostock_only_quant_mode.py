from datetime import date, timedelta

from datasource.models.market import KLineBar
from scripts.run_real_quant_top500 import _capital_flow_for_mode, _quant_mode


def _bars(count: int = 25) -> list[KLineBar]:
    start = date(2026, 6, 1)
    return [
        KLineBar(
            stock_code="000001",
            datetime=(start + timedelta(days=index)).isoformat(),
            open=10 + index * 0.1,
            high=10.5 + index * 0.1,
            low=9.5 + index * 0.1,
            close=10.2 + index * 0.1,
            pre_close=10 + index * 0.1,
            volume=1000 + index * 100,
            amount=10000000 + index * 100000,
            turnover_rate=1.0 + index * 0.01,
            change_percent=1.0,
            source="baostock",
        )
        for index in range(count)
    ]


def test_baostock_only_quant_mode_label() -> None:
    assert _quant_mode("baostock", "baostock") == "baostock_historical_degraded"
    assert _quant_mode("mock", "mock") == "standard"


def test_capital_score_can_use_amount_volume_turnover_proxy() -> None:
    flow = _capital_flow_for_mode(
        "baostock_historical_degraded",
        "000001",
        _bars(),
        external_flow=None,
        trade_date=date(2026, 7, 8),
    )

    assert flow is not None
    assert flow.stock_code == "000001"
    assert flow.turnover_rate > 0
    assert flow.volume_ratio > 0
