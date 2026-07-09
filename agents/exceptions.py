from __future__ import annotations


class AgentError(RuntimeError):
    """Base error for AI committee agent failures."""


class AgentConfigError(AgentError):
    """Raised when AI committee configuration is invalid."""


class AgentParseError(AgentError):
    """Raised when an agent returns unparseable output."""


class CommitteePersistenceError(AgentError):
    """Raised when committee results cannot be persisted."""
