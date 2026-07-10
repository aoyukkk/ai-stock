class LLMGatewayError(RuntimeError):
    """Base LLM Gateway error."""


class LLMProviderNotFoundError(LLMGatewayError):
    """Raised when a provider is not registered."""


class LLMProviderUnavailableError(LLMGatewayError):
    """Raised when a provider is disabled or unavailable."""


class LLMProviderNotConfiguredError(LLMProviderUnavailableError):
    """Raised when a provider has no configured credential."""


class LLMAuthenticationError(LLMGatewayError):
    """Raised for non-retryable provider authentication failures."""


class LLMInsufficientBalanceError(LLMGatewayError):
    """Raised when the provider account cannot pay for the request."""


class LLMRateLimitError(LLMGatewayError):
    """Raised after bounded retries for provider rate limiting."""


class LLMTimeoutError(LLMGatewayError):
    """Raised after bounded retries for provider timeouts."""


class LLMProviderResponseError(LLMGatewayError):
    """Raised for malformed or failed provider responses."""


class LLMSchemaValidationError(LLMGatewayError):
    """Raised when structured output cannot be validated or repaired."""


class LLMModelNotAvailableError(LLMGatewayError):
    """Raised when the configured model is absent from the provider model list."""


class PromptNotFoundError(LLMGatewayError):
    """Raised when a prompt template is missing."""


PROVIDER_DISABLED_MESSAGE = "Provider is disabled or not implemented in current phase."
