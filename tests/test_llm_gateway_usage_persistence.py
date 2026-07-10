from sqlalchemy import select

import database.models  # noqa: F401
from database.base import Base
from database.models.system import LLMUsage
from database.session import create_engine_from_url, get_session
from llm_gateway.cache import InMemoryLLMCache
from llm_gateway.registry import create_default_registry
from llm_gateway.router import LLMRouter, LLMRouterConfig
from llm_gateway.schemas import LLMMessage, LLMRequest


def _config() -> LLMRouterConfig:
    return LLMRouterConfig(
        mock_only=False,
        default_provider="mock",
        default_model="mock-chat",
        cache_enabled=True,
        cache_ttl_seconds=60,
        routing={"default": {"model_alias": "mock-fast"}},
        fallback={"final": "mock"},
        budgets={},
        aliases={"mock-fast": {"provider": "mock", "model": "mock-fast"}},
    )


def test_success_and_cache_hit_usage_are_persisted_with_prompt_version() -> None:
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    try:
        router = LLMRouter(_config(), registry=create_default_registry(), cache=InMemoryLLMCache(), db_session=session)
        request = LLMRequest(
            agent_name="usage_agent",
            task="usage_test",
            model_alias="mock-fast",
            messages=[LLMMessage(role="user", content="hello")],
            prompt_version="usage-v1",
        )

        first = router.chat(request)
        second = router.chat(request)
        rows = session.scalars(select(LLMUsage).order_by(LLMUsage.id)).all()

        assert len(rows) == 2
        assert rows[0].status == "ok"
        assert rows[0].prompt_version == "usage-v1"
        assert rows[0].request_hash == first.request_hash == second.request_hash
        assert rows[0].cached_input_tokens == 0
        assert rows[1].cached_input_tokens == rows[1].input_tokens
    finally:
        session.close()
        engine.dispose()


def test_failed_usage_is_persisted_without_secret_content() -> None:
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    try:
        router = LLMRouter(_config(), registry=create_default_registry(), db_session=session)
        response = router.chat(
            LLMRequest(
                agent_name="usage_agent",
                task="failure_test",
                model_alias="missing-alias",
                messages=[LLMMessage(role="user", content="hello")],
                allow_fallback=False,
            )
        )
        row = session.scalar(select(LLMUsage))

        assert response.status == "provider_error"
        assert row is not None and row.status == "provider_error"
        assert "authorization" not in (row.error_message or "").lower()
    finally:
        session.close()
        engine.dispose()
