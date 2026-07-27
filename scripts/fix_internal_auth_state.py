from __future__ import annotations

"""Repair obsolete forced-reset state for the one shared internal password.

``--dry-run`` opens SQLite in immutable read-only mode.  It never initializes a
schema, synchronizes a user, writes an audit record, or commits a transaction.
``--apply`` preserves the password hash and only clears the obsolete flag and
revokes existing application sessions.
"""

import argparse
import hashlib
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.internal_auth import sync_internal_users
from backend.core.internal_settings import load_internal_web_settings
from database.models.internal_auth import InternalAuditEvent, InternalAuthSession, InternalPasswordCredential, InternalUser
from database.session import get_database_identity, get_database_url, get_session, init_db


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair shared-password forced-reset state")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--dry-run", action="store_true")
    action.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    _load_service_environment()
    settings = load_internal_web_settings()
    if not settings.shared_password_enabled:
        raise RuntimeError("LOCAL_SHARED_PASSWORD_MODE_REQUIRED")
    database_path = _sqlite_path()
    if args.dry_run:
        print(_read_only_report(database_path))
        return 0
    return _apply(database_path, settings)


def _load_service_environment() -> None:
    path = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "AITraderAssistant" / "config" / "internal-web.env"
    try:
        load_dotenv(path, override=False)
    except OSError:
        pass


def _sqlite_path() -> Path:
    value = get_database_identity(get_database_url()).get("absolute_path")
    if not value:
        raise RuntimeError("INTERNAL_AUTH_SQLITE_DATABASE_REQUIRED")
    return Path(value)


def _read_only_report(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise RuntimeError("INTERNAL_AUTH_DATABASE_NOT_FOUND")
    before_hash = _file_hash(path)
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = {row[1] for row in connection.execute("PRAGMA table_info(internal_password_credential)")} if "internal_password_credential" in tables else set()
        user_columns = {row[1] for row in connection.execute("PRAGMA table_info(internal_user)")} if "internal_user" in tables else set()
        migration_required = not {"internal_user", "internal_password_credential", "internal_auth_session", "internal_audit_event"}.issubset(tables)
        migration_required = migration_required or not {"must_change_password", "session_version"}.issubset(columns)
        migration_required = migration_required or "user_key" not in user_columns
        shared = None
        if "internal_user" in tables:
            key_column = "user_key" if "user_key" in user_columns else "email"
            key_value = "shared_internal_user" if key_column == "user_key" else "shared-internal-user@local.invalid"
            shared = connection.execute(
                f"SELECT id FROM internal_user WHERE {key_column} = ? LIMIT 1", (key_value,)
            ).fetchone()
        credential = None
        sessions = 0
        if shared and "internal_password_credential" in tables:
            credential = connection.execute(
                "SELECT password_hash, must_change_password FROM internal_password_credential WHERE internal_user_id = ?", (shared[0],)
            ).fetchone()
            if "internal_auth_session" in tables:
                sessions = connection.execute(
                    "SELECT COUNT(*) FROM internal_auth_session WHERE internal_user_id = ? AND revoked_at IS NULL", (shared[0],)
                ).fetchone()[0]
        return {
            "database_path": str(path), "schema_version": "internal_auth_hotfix_v1",
            "shared_user_found": bool(shared), "password_hash_configured": bool(credential and credential[0]),
            "must_change_password_current": bool(credential and credential[1]),
            "planned_must_change_password": False, "sessions_found": sessions,
            "planned_sessions_to_revoke": sessions, "migration_required": migration_required,
            "database_file_hash": before_hash, "dry_run_read_only": before_hash == _file_hash(path),
        }
    finally:
        connection.close()


def _apply(path: Path, settings) -> int:
    init_db()
    session = get_session()
    try:
        sync_internal_users(session, settings)
        user = session.scalar(select(InternalUser).where(InternalUser.user_key == "shared_internal_user"))
        if user is None:
            raise RuntimeError("SHARED_INTERNAL_USER_NOT_CONFIGURED")
        credential = session.scalar(select(InternalPasswordCredential).where(InternalPasswordCredential.internal_user_id == user.id))
        if credential is None or not credential.password_hash:
            raise RuntimeError("SHARED_PASSWORD_HASH_NOT_CONFIGURED_RUN_SET_SCRIPT")
        now = datetime.now(timezone.utc)
        credential.must_change_password = False
        credential.session_version += 1
        revoked = 0
        for auth_session in session.scalars(select(InternalAuthSession).where(
            InternalAuthSession.internal_user_id == user.id, InternalAuthSession.revoked_at.is_(None),
        )):
            auth_session.revoked_at = now
            revoked += 1
        session.add(InternalAuditEvent(
            access_email=user.email, internal_user_id=user.id, role=user.role,
            operation="AUTH_FORCE_PASSWORD_CHANGE_CLEARED", entity_type="internal_user", entity_id=str(user.id),
            metadata_json={"sessions_revoked": revoked}, created_at=now,
        ))
        session.commit()
        print({"database_path": str(path), "shared_user": "shared_internal_user", "password_hash_changed": False,
               "must_change_password": False, "sessions_revoked": revoked, "updated_at": now.isoformat()})
        return 0
    finally:
        session.close()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
