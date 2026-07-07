import importlib
import sys

from sqlalchemy.orm import Session

from backend.core.config import get_settings


def test_database_session_import_does_not_create_engine(monkeypatch) -> None:
    import sqlalchemy

    calls = []
    sys.modules.pop("database.session", None)

    def fake_create_engine(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("create_engine should not run during import")

    with monkeypatch.context() as patcher:
        patcher.setattr(sqlalchemy, "create_engine", fake_create_engine)
        module = importlib.import_module("database.session")

    importlib.reload(module)
    assert calls == []


def test_resolve_database_url_uses_sqlite_memory_fallback() -> None:
    from database.session import SQLITE_MEMORY_URL, resolve_database_url

    assert resolve_database_url("") == SQLITE_MEMORY_URL


def test_resolve_database_url_reads_config(monkeypatch) -> None:
    from database.session import resolve_database_url

    configured_url = "postgresql+psycopg2://user:pass@localhost:5432/testdb"
    monkeypatch.setenv("DATABASE_URL", configured_url)
    get_settings.cache_clear()

    try:
        assert resolve_database_url() == configured_url
    finally:
        get_settings.cache_clear()


def test_create_session_factory_with_sqlite_memory() -> None:
    from database.session import SQLITE_MEMORY_URL, create_db_engine, create_session_factory

    engine = create_db_engine(SQLITE_MEMORY_URL)
    session_factory = create_session_factory(engine=engine)

    with session_factory() as session:
        assert isinstance(session, Session)


def test_get_db_session_yields_session() -> None:
    from database.session import SQLITE_MEMORY_URL, create_db_engine, create_session_factory, get_db_session

    engine = create_db_engine(SQLITE_MEMORY_URL)
    session_factory = create_session_factory(engine=engine)
    session_generator = get_db_session(session_factory)
    session = next(session_generator)

    try:
        assert isinstance(session, Session)
    finally:
        try:
            next(session_generator)
        except StopIteration:
            pass
