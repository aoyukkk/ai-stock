class QuantError(RuntimeError):
    """Base quant engine error."""


class InvalidQuantConfigError(QuantError):
    """Raised when quant configuration is invalid."""
