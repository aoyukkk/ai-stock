from __future__ import annotations


class VirtualTradingError(RuntimeError):
    """Base error for virtual trading simulation."""


class VirtualTradingConfigError(VirtualTradingError):
    """Raised when virtual trading configuration is unsafe or invalid."""


class TradingRuleError(VirtualTradingError):
    """Raised when an A-share virtual trading rule is violated."""


class VirtualBrokerError(VirtualTradingError):
    """Raised when the virtual broker cannot complete an operation."""
