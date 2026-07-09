from sqlalchemy import inspect

import database.models  # noqa: F401
from database.base import Base
from database.session import create_engine_from_url, get_session


def test_sqlite_memory_engine_session_and_create_all() -> None:
    engine = create_engine_from_url("sqlite:///:memory:")
    session = get_session(engine)

    try:
        Base.metadata.create_all(bind=engine)
        tables = set(inspect(engine).get_table_names())

        assert "stock_master" in tables
        assert "order_plan" in tables
        assert session.is_active
    finally:
        session.close()
        engine.dispose()
