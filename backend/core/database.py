from __future__ import annotations

from collections.abc import Generator

from sqlalchemy.orm import Session

from database.session import get_session


def get_db() -> Generator[Session, None, None]:
    session = get_session()
    try:
        yield session
    finally:
        session.close()
