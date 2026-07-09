class ReviewError(RuntimeError):
    """Base exception for daily review evaluation errors."""


class ReviewConfigError(ReviewError):
    """Raised when review configuration is unsafe or invalid."""


class ReviewNotFoundError(ReviewError):
    """Raised when a persisted daily review cannot be found."""
