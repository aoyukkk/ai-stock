class LightScreeningError(RuntimeError):
    """Base light screening error."""


class LightScreeningConfigError(LightScreeningError):
    """Raised when light screening configuration is invalid."""


class LightScreeningParseError(LightScreeningError):
    """Raised when LLM light screening output cannot be parsed."""


class LightScreeningPersistenceError(LightScreeningError):
    """Raised when light screening persistence fails."""
