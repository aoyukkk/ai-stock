class LLMGatewayError(RuntimeError):
    """Base LLM Gateway error."""


class LLMProviderNotFoundError(LLMGatewayError):
    """Raised when a provider is not registered."""


class LLMProviderUnavailableError(LLMGatewayError):
    """Raised when a provider is disabled or unavailable."""


class PromptNotFoundError(LLMGatewayError):
    """Raised when a prompt template is missing."""


PROVIDER_DISABLED_MESSAGE = "Provider is disabled or not implemented in current phase."
