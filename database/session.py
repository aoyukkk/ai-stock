from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from backend.core.config import get_settings


SQLITE_MEMORY_URL = "sqlite+pysqlite:///:memory:"


def resolve_database_url(database_url: str | None = None) -> str:
    if database_url is not None:
        return database_url or SQLITE_MEMORY_URL

    settings = get_settings()
    return settings.database_url or SQLITE_MEMORY_URL


def create_db_engine(database_url: str | None = None, **kwargs) -> Engine:
    url = resolve_database_url(database_url)
    connect_args = kwargs.pop("connect_args", {})

    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False, **connect_args}

    return create_engine(
        url,
        future=True,
        connect_args=connect_args,
        **kwargs,
    )


def create_session_factory(
    engine: Engine | None = None,
    database_url: str | None = None,
) -> sessionmaker[Session]:
    bind = engine or create_db_engine(database_url)
    return sessionmaker(
        bind=bind,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )


def get_db_session(
    session_factory: sessionmaker[Session] | None = None,
) -> Generator[Session, None, None]:
    factory = session_factory or create_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()
