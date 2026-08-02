from __future__ import annotations

"""Apply the internal-auth hotfix to SQLite in one idempotent transaction."""

import os
import sqlite3
from pathlib import Path


DATABASE = Path(os.environ.get("AI_TRADER_DB_PATH", r"C:\ProgramData\AITraderAssistant\data\ai_trader_internal.db"))


def columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def main() -> int:
    connection = sqlite3.connect(DATABASE)
    try:
        connection.execute("BEGIN IMMEDIATE")
        user_columns = columns(connection, "internal_user")
        credential_columns = columns(connection, "internal_password_credential")
        required_tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"internal_user", "internal_password_credential", "internal_auth_session", "internal_audit_event"}.issubset(required_tables):
            raise RuntimeError("INTERNAL_AUTH_SCHEMA_MISSING")
        if "user_key" not in user_columns:
            connection.execute("ALTER TABLE internal_user ADD COLUMN user_key VARCHAR(96)")
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_internal_user_user_key ON internal_user(user_key)")
        additions = {
            "password_initialized": "BOOLEAN NOT NULL DEFAULT 0",
            "password_changed_at": "DATETIME",
            "session_version": "INTEGER NOT NULL DEFAULT 1",
        }
        for name, definition in additions.items():
            if name not in credential_columns:
                connection.execute(f"ALTER TABLE internal_password_credential ADD COLUMN {name} {definition}")
        connection.execute("UPDATE internal_password_credential SET must_change_password = 0 WHERE must_change_password = 1")
        connection.commit()
        print({"migration": "20260722_internal_auth_hotfix", "committed": True})
        return 0
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
