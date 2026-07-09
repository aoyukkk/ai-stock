class DataSourceError(RuntimeError):
    """Base data source error."""


class ProviderNotFoundError(DataSourceError):
    """Raised when a provider is not registered."""


class ProviderDisabledError(DataSourceError):
    """Raised when a provider is disabled for the current phase."""


class ProviderNotImplementedError(DataSourceError):
    """Raised by real provider placeholders in Phase 3."""


NOT_IMPLEMENTED_MESSAGE = "Provider is not implemented or disabled in current phase."
