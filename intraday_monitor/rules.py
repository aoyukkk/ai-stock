from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


PRICE_RULES = {
    "ENTERED_CONSERVATIVE_ZONE", "ENTERED_BALANCED_ZONE", "ENTRY_TRIGGER_REACHED",
    "ABOVE_MAX_ACCEPTABLE_PRICE", "PRICE_DEVIATION_TOO_LARGE", "RISK_REWARD_INVALIDATED",
    "NEAR_LIMIT_UP", "AT_LIMIT_UP", "NEAR_LIMIT_DOWN", "AT_LIMIT_DOWN", "PLAN_EXPIRING", "PLAN_EXPIRED",
}
POSITION_RULES = {
    "APPROACHING_STOP_LOSS", "STOP_LOSS_BREACHED", "TAKE_PROFIT_1_REACHED", "TAKE_PROFIT_2_REACHED",
    "PROFIT_DRAWDOWN", "INTRADAY_HIGH_DRAWDOWN", "RAPID_POSITION_LOSS", "T_PLUS_ONE_LOCKED",
    "SELLABLE_QUANTITY_ZERO", "NEAR_LIMIT_DOWN_POSITION", "POSITION_DATA_STALE",
}
MINUTE_RULES = {
    "RAPID_RISE_5M", "RAPID_DROP_5M", "RAPID_RISE_15M", "RAPID_DROP_15M", "VWAP_BREAKOUT",
    "VWAP_BREAKDOWN", "VWAP_RECOVERY", "INTRADAY_NEW_HIGH", "INTRADAY_NEW_LOW",
    "SHARP_PULLBACK_FROM_HIGH", "LATE_RECOVERY", "VOLUME_EXPANSION", "PRICE_VOLUME_DIVERGENCE",
}
RELATIVE_RULES = {
    "RELATIVE_STRENGTHENING", "RELATIVE_WEAKENING", "DEFENSIVE_STRENGTH", "FAILING_TO_FOLLOW_MARKET",
    "MARKET_DOWN_STOCK_RESILIENT", "MARKET_UP_STOCK_LAGGING",
}
SYSTEM_RULES = {
    "DATA_STALE", "PROVIDER_AUTH_FAILED", "PROVIDER_RATE_LIMITED", "PROVIDER_TIMEOUT", "MINUTE_BAR_GAP",
    "PROVIDER_TIME_UNKNOWN", "DUAL_SOURCE_MATERIAL_CONFLICT", "DB_WRITE_FAILED", "CALL_BUDGET_WARNING",
    "CALL_BUDGET_BLOCKED", "MIDDAY_RESOURCE_PREEMPTION", "MONITOR_REFRESH_LAGGING",
}


@dataclass(frozen=True)
class RuleHit:
    rule_type: str
    severity: str
    current_value: Any
    threshold: Any
    comparison: str
    title: str
    message: str


class IntradayAlertRuleEngine:
    """Deterministic evaluator. It never calls an LLM or creates an order."""

    def evaluate(self, rule: Any, context: dict[str, Any]) -> RuleHit | None:
        rule_type = str(rule.rule_type)
        if rule_type in POSITION_RULES and context.get("monitor_profile") != "POSITION_RISK_MONITOR":
            return None
        if rule_type in PRICE_RULES and context.get("freshness_status") != "FRESH":
            return None
        threshold = dict(rule.threshold_json or {})
        current = self._current_value(rule_type, context)
        target = threshold.get("value")
        if not self._matches(rule.comparison, current, target):
            return None
        label = threshold.get("label") or rule_type
        return RuleHit(
            rule_type=rule_type,
            severity=str(rule.severity),
            current_value=current,
            threshold=target,
            comparison=str(rule.comparison),
            title=f"{context.get('stock_name') or context.get('stock_code')}：{label}",
            message=f"规则 {rule_type} 已触发，请交易员人工复核。仅供人工确认，不自动下单。",
        )

    def minute_features(self, bars: list[dict[str, Any]]) -> dict[str, float | None]:
        valid = [row for row in bars if row.get("close") is not None]
        if not valid:
            return {key: None for key in ("last_5m_return", "last_15m_return", "last_30m_return", "minute_vwap", "price_vs_vwap", "minute_volume_ratio", "drawdown_from_intraday_high", "recovery_from_intraday_low", "close_location_in_recent_range")}
        closes = [float(row["close"]) for row in valid]
        volumes = [float(row.get("volume") or 0) for row in valid]
        vwap = sum(p * v for p, v in zip(closes, volumes)) / sum(volumes) if sum(volumes) else None
        high, low, last = max(closes), min(closes), closes[-1]
        def ret(period: int) -> float | None:
            return ((last / closes[-period - 1]) - 1) * 100 if len(closes) > period and closes[-period - 1] else None
        base_volume = sum(volumes[:-5]) / max(1, len(volumes[:-5])) if len(volumes) > 5 else 0
        return {
            "last_5m_return": ret(5), "last_15m_return": ret(15), "last_30m_return": ret(30),
            "minute_vwap": vwap, "price_vs_vwap": ((last / vwap) - 1) * 100 if vwap else None,
            "minute_volume_ratio": (sum(volumes[-5:]) / 5) / base_volume if base_volume else None,
            "drawdown_from_intraday_high": ((last / high) - 1) * 100 if high else None,
            "recovery_from_intraday_low": ((last / low) - 1) * 100 if low else None,
            "close_location_in_recent_range": (last - low) / (high - low) if high != low else 0.5,
        }

    @staticmethod
    def _current_value(rule_type: str, context: dict[str, Any]) -> Any:
        mapping = {
            "ABOVE_MAX_ACCEPTABLE_PRICE": "latest", "APPROACHING_STOP_LOSS": "latest",
            "STOP_LOSS_BREACHED": "latest", "TAKE_PROFIT_1_REACHED": "latest", "TAKE_PROFIT_2_REACHED": "latest",
            "NEAR_LIMIT_UP": "limit_up_distance", "AT_LIMIT_UP": "limit_up_distance",
            "NEAR_LIMIT_DOWN": "limit_down_distance", "AT_LIMIT_DOWN": "limit_down_distance",
            "RAPID_RISE_5M": "last_5m_return", "RAPID_DROP_5M": "last_5m_return",
            "RAPID_RISE_15M": "last_15m_return", "RAPID_DROP_15M": "last_15m_return",
            "VWAP_BREAKOUT": "price_vs_vwap", "VWAP_BREAKDOWN": "price_vs_vwap",
            "RELATIVE_STRENGTHENING": "relative_index_return", "RELATIVE_WEAKENING": "relative_index_return",
            "DATA_STALE": "age_seconds", "SELLABLE_QUANTITY_ZERO": "sellable_quantity",
        }
        return context.get(mapping.get(rule_type, "value"))

    @staticmethod
    def _matches(comparison: str, current: Any, target: Any) -> bool:
        if current is None or target is None:
            return False
        operators = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b, "<": lambda a, b: a < b, "<=": lambda a, b: a <= b, "==": lambda a, b: a == b}
        try:
            return bool(operators.get(comparison, lambda _a, _b: False)(float(current), float(target)))
        except (TypeError, ValueError):
            return comparison == "==" and current == target


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
