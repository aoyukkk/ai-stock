from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from database.base import Base


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SQLITE_PATH = (ROOT_DIR / "data" / "ai_trader_dev.db").resolve()
DEFAULT_SQLITE_URL = f"sqlite:///{DEFAULT_SQLITE_PATH.as_posix()}"

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    class_=Session,
)

_engine: Engine | None = None


class DatabaseError(RuntimeError):
    """Raised when database infrastructure cannot be initialized."""


def get_database_url() -> str:
    database_path = os.getenv("AI_TRADER_DB_PATH", "").strip()
    if database_path:
        return f"sqlite:///{Path(database_path).expanduser().resolve().as_posix()}"
    return os.getenv("DATABASE_URL") or DEFAULT_SQLITE_URL


def get_database_type(database_url: str | None = None) -> str:
    url = database_url or get_database_url()
    try:
        drivername = make_url(url).drivername
    except Exception:
        return "unknown"

    if drivername.startswith("sqlite"):
        return "sqlite"
    if drivername.startswith("postgresql"):
        return "postgresql"
    return drivername.split("+", maxsplit=1)[0] or "unknown"


def get_database_identity(database_url: str | None = None) -> dict[str, str | None]:
    """Return a secret-free identity suitable for startup consistency checks."""
    url = make_url(database_url or get_database_url())
    database = url.database
    absolute_path = None
    filename = database
    if url.drivername.startswith("sqlite") and database and database != ":memory:":
        path = Path(database).expanduser().resolve()
        absolute_path = str(path)
        filename = path.name
    return {
        "dialect": url.get_backend_name(),
        "database_filename": filename,
        "absolute_path": absolute_path,
        "schema": url.query.get("schema"),
        "environment": os.getenv("APP_ENV", "development"),
    }


def assert_database_path_consistency(*database_urls: str) -> dict[str, str | None]:
    identities = [get_database_identity(value) for value in database_urls if value]
    if not identities:
        identities = [get_database_identity()]
    keys = {(item["dialect"], item["absolute_path"] or item["database_filename"], item["schema"]) for item in identities}
    if len(keys) != 1:
        raise DatabaseError("DATABASE_PATH_MISMATCH")
    return identities[0]


def create_engine_from_url(database_url: str) -> Engine:
    kwargs: dict = {
        "future": True,
        "pool_pre_ping": True,
    }

    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if database_url != "sqlite:///:memory:":
            _ensure_sqlite_parent_dir(database_url)

    try:
        return create_engine(database_url, **kwargs)
    except SQLAlchemyError as exc:
        raise DatabaseError("Failed to create database engine") from exc


def get_engine(database_url: str | None = None) -> Engine:
    global _engine

    if database_url is not None:
        return create_engine_from_url(database_url)

    if _engine is None:
        _engine = create_engine_from_url(get_database_url())
        SessionLocal.configure(bind=_engine)

    return _engine


def get_session(engine: Engine | None = None) -> Session:
    bind = engine or get_engine()
    if engine is not None:
        return sessionmaker(
            bind=bind,
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
            class_=Session,
        )()

    return SessionLocal()


def init_db(engine: Engine | None = None) -> None:
    import database.models  # noqa: F401

    bind = engine or get_engine()
    Base.metadata.create_all(bind=bind)


def close_db() -> None:
    global _engine

    if _engine is not None:
        _engine.dispose()
        _engine = None


def _ensure_sqlite_parent_dir(database_url: str) -> None:
    url = make_url(database_url)
    database = url.database
    if not database or database == ":memory:":
        return

    Path(database).expanduser().parent.mkdir(parents=True, exist_ok=True)
