from __future__ import annotations

from sqlalchemy import Engine

from database.base import Base
from database.session import get_engine


def create_all_tables(engine: Engine | None = None) -> None:
    import database.models  # noqa: F401

    bind = engine or get_engine()
    Base.metadata.create_all(bind=bind)


def init_database(engine: Engine | None = None) -> None:
    create_all_tables(engine)
