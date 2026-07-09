class MemoryError(RuntimeError):
    """Base exception for memory system errors."""


class MemoryConfigError(MemoryError):
    """Raised when memory configuration is unsafe or invalid."""


class MemoryNotFoundError(MemoryError):
    """Raised when a memory note or related record cannot be found."""


class MemoryValidationError(MemoryError):
    """Raised when memory input is invalid."""
