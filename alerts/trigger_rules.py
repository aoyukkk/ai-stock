from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from datasource.schemas import NewsItem
from alerts.config import AlertRulesConfig
from alerts.schemas import AlertEvent


def detect_price_move(stock_code: str, latest_change_percent: Decimal, config: AlertRulesConfig) -> AlertEvent | None:
    rise_threshold = config.price["rapid_rise_percent"]
    drop_threshold = config.price["rapid_drop_percent"]
    if latest_change_percent >= rise_threshold:
        return _event(
            stock_code,
            "PRICE_RAPID_RISE",
            "HIGH",
            f"{stock_code} rapid rise {latest_change_percent}%.",
            latest_change_percent,
            rise_threshold,
            "mock_quote",
            "RISK_ALERT",
        )
    if latest_change_percent <= drop_threshold:
        return _event(
            stock_code,
            "PRICE_RAPID_DROP",
            "HIGH",
            f"{stock_code} rapid drop {latest_change_percent}%.",
            latest_change_percent,
            drop_threshold,
            "mock_quote",
            "WATCH_ONLY",
        )
    return None


def detect_volume_abnormal(current_volume: int, average_volume: int, config: AlertRulesConfig, stock_code: str = "") -> AlertEvent | None:
    if average_volume <= 0:
        return None
    ratio = Decimal(current_volume) / Decimal(average_volume)
    threshold = config.volume["abnormal_ratio"]
    if ratio >= threshold:
        return _event(stock_code, "VOLUME_ABNORMAL", "MEDIUM", "Volume abnormal.", ratio, threshold, "mock_quote", "RISK_ALERT")
    return None


def detect_turnover_abnormal(current_turnover: Decimal, average_turnover: Decimal, config: AlertRulesConfig, stock_code: str = "") -> AlertEvent | None:
    if average_turnover <= 0:
        return None
    ratio = current_turnover / average_turnover
    threshold = config.turnover["abnormal_ratio"]
    if ratio >= threshold:
        return _event(stock_code, "TURNOVER_ABNORMAL", "MEDIUM", "Turnover abnormal.", ratio, threshold, "mock_capital_flow", "RISK_ALERT")
    return None


def detect_important_news(news_item: NewsItem, config: AlertRulesConfig) -> AlertEvent | None:
    threshold = config.news["importance_threshold"]
    if news_item.importance >= threshold:
        action = "BLOCK" if str(news_item.sentiment).upper() == "NEGATIVE" else "RISK_ALERT"
        severity = "CRITICAL" if action == "BLOCK" else "HIGH"
        stock_code = news_item.related_stocks[0] if news_item.related_stocks else ""
        return _event(stock_code, "NEWS_IMPORTANT", severity, news_item.title, news_item.importance, threshold, "mock_news", action)
    return None


def detect_auction_abnormal(auction_change_percent: Decimal, config: AlertRulesConfig, stock_code: str = "") -> AlertEvent | None:
    if not bool(config.pre_market.get("auction_abnormal_enabled", True)):
        return None
    high = Decimal(str(config.pre_market["high_open_cancel_threshold_percent"]))
    low = Decimal(str(config.pre_market["low_open_recheck_threshold_percent"]))
    if auction_change_percent >= high:
        return _event(stock_code, "AUCTION_ABNORMAL", "HIGH", "High-open auction abnormal.", auction_change_percent, high, "mock_auction", "CANCEL")
    if auction_change_percent <= low:
        return _event(stock_code, "AUCTION_ABNORMAL", "MEDIUM", "Low-open auction abnormal.", auction_change_percent, low, "mock_auction", "REPRICE")
    return None


def detect_pending_order_near_fill(order_price: Decimal, latest_price: Decimal, config: AlertRulesConfig, stock_code: str = "") -> AlertEvent | None:
    if order_price <= 0:
        return None
    diff_percent = abs(order_price - latest_price) / order_price * Decimal("100")
    threshold = config.order["near_fill_threshold_percent"]
    if diff_percent <= threshold:
        return _event(stock_code, "PENDING_ORDER_NEAR_FILL", "MEDIUM", "Pending virtual order is near fill.", diff_percent, threshold, "virtual_order", "RISK_ALERT")
    return None


def detect_limit_up_down(latest_price: Decimal, limit_up_price: Decimal, limit_down_price: Decimal, stock_code: str = "") -> AlertEvent | None:
    if latest_price >= limit_up_price:
        return _event(stock_code, "LIMIT_UP_DOWN", "HIGH", "Price is at or above limit-up.", latest_price, limit_up_price, "mock_quote", "WATCH_ONLY")
    if latest_price <= limit_down_price:
        return _event(stock_code, "LIMIT_UP_DOWN", "HIGH", "Price is at or below limit-down.", latest_price, limit_down_price, "mock_quote", "BLOCK")
    return None


def _event(
    stock_code: str,
    alert_type: str,
    severity: str,
    message: str,
    trigger_value: Decimal,
    threshold: Decimal,
    source: str,
    suggested_action: str,
) -> AlertEvent:
    return AlertEvent(
        stock_code=stock_code,
        alert_type=alert_type,  # type: ignore[arg-type]
        severity=severity,  # type: ignore[arg-type]
        message=message,
        trigger_value=trigger_value.quantize(Decimal("0.0001")),
        threshold=threshold.quantize(Decimal("0.0001")),
        source=source,
        created_at=datetime.now(timezone.utc),
        suggested_action=suggested_action,  # type: ignore[arg-type]
    )
