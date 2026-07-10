from __future__ import annotations

from decimal import Decimal
from typing import Any


LIMIT_STATUS_SCORES = {
    "NORMAL": Decimal("100"),
    "NEAR_LIMIT_UP": Decimal("72"),
    "AT_LIMIT_UP": Decimal("50"),
    "OPENED_LIMIT_UP": Decimal("45"),
    "CONSECUTIVE_LIMIT_UP": Decimal("30"),
    "NEAR_LIMIT_DOWN": Decimal("35"),
    "AT_LIMIT_DOWN": Decimal("15"),
    "OPENED_LIMIT_DOWN": Decimal("20"),
    "CONSECUTIVE_LIMIT_DOWN": Decimal("5"),
    "LIMIT_DATA_MISSING": Decimal("50"),
    "NOT_APPLICABLE": Decimal("50"),
}


def build_price_limit_risk(
    bars: list[Any],
    limit_records: dict[str, dict[str, Any]],
    *,
    near_limit_percent: Decimal = Decimal("0.01"),
    consecutive_window: int = 5,
    tick_size: Decimal = Decimal("0.01"),
) -> dict[str, Any]:
    if not bars:
        return _missing()
    latest = bars[-1]
    latest_record = limit_records.get(_compact_date(latest.trade_date))
    if not latest_record:
        return _missing()

    up_limit = _decimal(_pick(latest_record, "up_limit", "limit_up_price"))
    down_limit = _decimal(_pick(latest_record, "down_limit", "limit_down_price"))
    if up_limit <= 0 or down_limit <= 0:
        return _missing()

    tolerance = max(tick_size / Decimal("2"), Decimal("0.0001"))
    close = _decimal(latest.close)
    high = _decimal(latest.high)
    low = _decimal(latest.low)
    close_at_up = abs(close - up_limit) <= tolerance
    close_at_down = abs(close - down_limit) <= tolerance
    touched_up = high >= up_limit - tolerance
    touched_down = low <= down_limit + tolerance
    consecutive_up = _consecutive_count(bars, limit_records, "up", consecutive_window, tolerance)
    consecutive_down = _consecutive_count(bars, limit_records, "down", consecutive_window, tolerance)
    status = _status(
        close=close,
        up_limit=up_limit,
        down_limit=down_limit,
        near_limit_percent=near_limit_percent,
        touched_up=touched_up,
        touched_down=touched_down,
        close_at_up=close_at_up,
        close_at_down=close_at_down,
        consecutive_up=consecutive_up,
        consecutive_down=consecutive_down,
    )
    return {
        "limit_up_price": up_limit,
        "limit_down_price": down_limit,
        "limit_status": status,
        "distance_to_limit_up": _distance(close, up_limit),
        "distance_to_limit_down": _distance(close, down_limit),
        "touched_limit_up": touched_up,
        "touched_limit_down": touched_down,
        "close_at_limit_up": close_at_up,
        "close_at_limit_down": close_at_down,
        "consecutive_limit_up_count": consecutive_up,
        "consecutive_limit_down_count": consecutive_down,
        "price_limit_risk_score": LIMIT_STATUS_SCORES[status],
        "limit_risk_note": _risk_note(status),
    }


def _status(
    *,
    close: Decimal,
    up_limit: Decimal,
    down_limit: Decimal,
    near_limit_percent: Decimal,
    touched_up: bool,
    touched_down: bool,
    close_at_up: bool,
    close_at_down: bool,
    consecutive_up: int,
    consecutive_down: int,
) -> str:
    if close_at_down and consecutive_down >= 2:
        return "CONSECUTIVE_LIMIT_DOWN"
    if close_at_down:
        return "AT_LIMIT_DOWN"
    if touched_down:
        return "OPENED_LIMIT_DOWN"
    if close <= down_limit * (Decimal("1") + near_limit_percent):
        return "NEAR_LIMIT_DOWN"
    if close_at_up and consecutive_up >= 2:
        return "CONSECUTIVE_LIMIT_UP"
    if close_at_up:
        return "AT_LIMIT_UP"
    if touched_up:
        return "OPENED_LIMIT_UP"
    if close >= up_limit * (Decimal("1") - near_limit_percent):
        return "NEAR_LIMIT_UP"
    return "NORMAL"


def _consecutive_count(
    bars: list[Any],
    records: dict[str, dict[str, Any]],
    direction: str,
    window: int,
    tolerance: Decimal,
) -> int:
    count = 0
    for bar in reversed(bars[-max(1, window) :]):
        record = records.get(_compact_date(bar.trade_date))
        if not record:
            break
        limit = _decimal(_pick(record, "up_limit" if direction == "up" else "down_limit"))
        if limit <= 0 or abs(_decimal(bar.close) - limit) > tolerance:
            break
        count += 1
    return count


def _missing() -> dict[str, Any]:
    return {
        "limit_up_price": None,
        "limit_down_price": None,
        "limit_status": "LIMIT_DATA_MISSING",
        "distance_to_limit_up": None,
        "distance_to_limit_down": None,
        "touched_limit_up": False,
        "touched_limit_down": False,
        "close_at_limit_up": False,
        "close_at_limit_down": False,
        "consecutive_limit_up_count": 0,
        "consecutive_limit_down_count": 0,
        "price_limit_risk_score": LIMIT_STATUS_SCORES["LIMIT_DATA_MISSING"],
        "limit_risk_note": "stk_limit unavailable; neutral conservative score",
    }


def _risk_note(status: str) -> str:
    if status in {"AT_LIMIT_DOWN", "OPENED_LIMIT_DOWN", "CONSECUTIVE_LIMIT_DOWN", "NEAR_LIMIT_DOWN"}:
        return "limit_down_liquidity_risk"
    if status in {"AT_LIMIT_UP", "OPENED_LIMIT_UP", "CONSECUTIVE_LIMIT_UP", "NEAR_LIMIT_UP"}:
        return "limit_up_chase_and_exit_risk"
    return "normal"


def _distance(close: Decimal, limit: Decimal) -> Decimal | None:
    if limit <= 0:
        return None
    return ((limit - close) / limit * Decimal("100")).quantize(Decimal("0.0001"))


def _compact_date(value: Any) -> str:
    if hasattr(value, "strftime"):
        return value.strftime("%Y%m%d")
    return str(value or "").split("T", 1)[0].replace("-", "")


def _pick(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        if row.get(name) not in (None, ""):
            return row[name]
    return 0


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value or 0))
