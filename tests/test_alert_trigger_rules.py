from datetime import datetime, timezone
from decimal import Decimal

from alerts.config import load_alert_rules_config
from alerts.trigger_rules import (
    detect_pending_order_near_fill,
    detect_price_move,
    detect_volume_abnormal,
)


def test_rapid_rise_triggers_alert() -> None:
    alert = detect_price_move("000001", Decimal("8.00"), load_alert_rules_config())

    assert alert is not None
    assert alert.alert_type == "PRICE_RAPID_RISE"
    assert alert.suggested_action == "RISK_ALERT"


def test_rapid_drop_triggers_alert() -> None:
    alert = detect_price_move("000001", Decimal("-5.00"), load_alert_rules_config())

    assert alert is not None
    assert alert.alert_type == "PRICE_RAPID_DROP"


def test_volume_abnormal_triggers_alert() -> None:
    alert = detect_volume_abnormal(300, 100, load_alert_rules_config(), stock_code="000001")

    assert alert is not None
    assert alert.alert_type == "VOLUME_ABNORMAL"


def test_pending_order_near_fill_triggers_alert() -> None:
    alert = detect_pending_order_near_fill(
        Decimal("10.00"),
        Decimal("10.02"),
        load_alert_rules_config(),
        stock_code="000001",
    )

    assert alert is not None
    assert alert.alert_type == "PENDING_ORDER_NEAR_FILL"


def test_threshold_boundary_normal_when_not_reached() -> None:
    assert detect_price_move("000001", Decimal("7.99"), load_alert_rules_config()) is None
    assert detect_volume_abnormal(299, 100, load_alert_rules_config(), stock_code="000001") is None


def test_no_trigger_returns_none() -> None:
    assert detect_pending_order_near_fill(
        Decimal("10.00"),
        Decimal("10.50"),
        load_alert_rules_config(),
        stock_code="000001",
    ) is None
