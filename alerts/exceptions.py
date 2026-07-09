from __future__ import annotations


class AlertError(RuntimeError):
    """Base error for alert and recheck workflows."""


class AlertConfigError(AlertError):
    """Raised when alert/recheck configuration is unsafe or invalid."""
