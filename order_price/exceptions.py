from __future__ import annotations


class OrderPriceError(RuntimeError):
    """Base error for order price evaluation."""


class OrderPriceConfigError(OrderPriceError):
    """Raised when order price configuration is invalid."""


class OrderPriceDataError(OrderPriceError):
    """Raised when required market data is missing or invalid."""


class OrderPricePersistenceError(OrderPriceError):
    """Raised when order plan drafts cannot be persisted."""
