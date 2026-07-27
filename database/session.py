from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

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
_auth_engine: Engine | None = None


class DatabaseError(RuntimeError):
    """Raised when database infrastructure cannot be initialized."""


def get_database_url() -> str:
    if os.getenv("APP_RUNTIME_MODE", "").strip().upper() == "INTERNAL_WEB_SERVER":
        path = os.getenv("INTERNAL_WEB_BUSINESS_DATABASE_PATH", "").strip()
        if not path:
            raise DatabaseError("INTERNAL_WEB_BUSINESS_DATABASE_PATH_REQUIRED")
        return _sqlite_readonly_url(Path(path))
    database_path = os.getenv("AI_TRADER_DB_PATH", "").strip()
    if database_path:
        return f"sqlite:///{Path(database_path).expanduser().resolve().as_posix()}"
    configured = os.getenv("DATABASE_URL")
    if configured:
        return configured
    if os.getenv("APP_RUNTIME_MODE", "").strip().upper() == "INTERNAL_WEB_SERVER":
        from backend.core.runtime_paths import server_data_root

        path = (server_data_root() / "data" / "ai_trader_internal.db").resolve()
        return f"sqlite:///{path.as_posix()}"
    return DEFAULT_SQLITE_URL


def get_auth_database_url() -> str:
    path = os.getenv("INTERNAL_WEB_AUTH_DATABASE_PATH", os.getenv("AI_TRADER_DB_PATH", "")).strip()
    if not path:
        raise DatabaseError("INTERNAL_WEB_AUTH_DATABASE_PATH_REQUIRED")
    return f"sqlite:///{Path(path).expanduser().resolve().as_posix()}"


def _sqlite_readonly_url(path: Path) -> str:
    return f"sqlite+pysqlite:///file:{quote(path.expanduser().resolve().as_posix())}?mode=ro&uri=true"


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
        if _is_readonly_sqlite(database_url):
            # A pooled SQLite handle prevents atomic snapshot replacement on
            # Windows. Internal Web only reads this database, so close every
            # connection at the end of its request.
            kwargs["poolclass"] = NullPool
        kwargs["connect_args"] = {
            "check_same_thread": False,
            "timeout": float(os.getenv("SQLITE_BUSY_TIMEOUT_SECONDS", "30")),
        }
        if database_url != "sqlite:///:memory:" and not _is_readonly_sqlite(database_url):
            _ensure_sqlite_parent_dir(database_url)

    try:
        engine = create_engine(database_url, **kwargs)
        if database_url.startswith("sqlite"):
            _configure_sqlite(engine, database_url)
        return engine
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


def get_auth_engine() -> Engine:
    global _auth_engine
    if _auth_engine is None:
        _auth_engine = create_engine_from_url(get_auth_database_url())
    return _auth_engine


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


def get_auth_session() -> Session:
    return sessionmaker(bind=get_auth_engine(), autocommit=False, autoflush=False,
                        expire_on_commit=False, class_=Session)()


def init_db(engine: Engine | None = None) -> None:
    import database.models  # noqa: F401

    bind = engine or get_engine()
    if _is_readonly_sqlite(bind.url.render_as_string(hide_password=True)):
        return
    Base.metadata.create_all(bind=bind)
    _ensure_internal_auth_hotfix_columns(bind)


def init_auth_db() -> None:
    init_db(get_auth_engine())


def close_db() -> None:
    global _engine, _auth_engine

    if _engine is not None:
        _engine.dispose()
        _engine = None
    if _auth_engine is not None:
        _auth_engine.dispose()
        _auth_engine = None


def _ensure_sqlite_parent_dir(database_url: str) -> None:
    url = make_url(database_url)
    database = url.database
    if not database or database == ":memory:":
        return

    Path(database).expanduser().parent.mkdir(parents=True, exist_ok=True)


def _configure_sqlite(engine: Engine, database_url: str) -> None:
    busy_timeout_ms = int(float(os.getenv("SQLITE_BUSY_TIMEOUT_SECONDS", "30")) * 1000)
    persistent = database_url != "sqlite:///:memory:" and not _is_readonly_sqlite(database_url)

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
            if persistent:
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()


def _is_readonly_sqlite(database_url: str) -> bool:
    return "mode=ro" in database_url and "uri=true" in database_url


def _ensure_internal_auth_hotfix_columns(engine: Engine) -> None:
    """Upgrade the small internal-auth table in-place for existing SQLite installs.

    This is deliberately idempotent: the packaged internal service has no general
    migration runner, and the origin must not start against a stale auth schema.
    """
    if engine.dialect.name != "sqlite":
        return
    with engine.begin() as connection:
        exists = connection.execute(text(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='internal_password_credential'"
        )).scalar()
        if not exists:
            return
        user_columns = {row[1] for row in connection.execute(text("PRAGMA table_info(internal_user)"))}
        if "user_key" not in user_columns:
            connection.execute(text("ALTER TABLE internal_user ADD COLUMN user_key VARCHAR(96)"))
            connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_internal_user_user_key ON internal_user(user_key)"))
        columns = {row[1] for row in connection.execute(text("PRAGMA table_info(internal_password_credential)"))}
        additions = {
            "password_initialized": "BOOLEAN NOT NULL DEFAULT 0",
            "password_changed_at": "DATETIME",
            "session_version": "INTEGER NOT NULL DEFAULT 1",
        }
        for name, definition in additions.items():
            if name not in columns:
                connection.execute(text(f"ALTER TABLE internal_password_credential ADD COLUMN {name} {definition}"))
