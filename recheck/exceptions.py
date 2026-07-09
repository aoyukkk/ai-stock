from __future__ import annotations


class RecheckError(RuntimeError):
    """Base error for order reassessment workflows."""


class OrderPlanNotFoundError(RecheckError):
    """Raised when an order plan cannot be found."""
