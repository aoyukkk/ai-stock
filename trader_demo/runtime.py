from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from backend.core.config import get_app_config
from llm_gateway.registry import get_llm_provider_registry
from llm_gateway.service import get_llm_gateway_service


REAL_LLM_ENV = {
    "LLM_REAL_CALLS_ENABLED": "true",
    "RUN_REAL_FUNDAMENTAL_RESEARCH": "true",
    "LLM_GATEWAY_MOCK_ONLY": "false",
}


def clear_llm_runtime_caches() -> None:
    get_llm_gateway_service.cache_clear()
    get_llm_provider_registry.cache_clear()
    get_app_config.cache_clear()


@contextmanager
def temporary_real_llm_runtime() -> Iterator[None]:
    """Enable guarded real calls for one command and restore every env value."""

    previous = {key: os.environ.get(key) for key in REAL_LLM_ENV}
    try:
        os.environ.update(REAL_LLM_ENV)
        clear_llm_runtime_caches()
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        clear_llm_runtime_caches()
