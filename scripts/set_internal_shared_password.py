from __future__ import annotations

import argparse
import ctypes
import getpass
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.internal_auth import LocalPasswordAuthService, sync_internal_users
from backend.core.internal_settings import load_internal_web_settings
from database.models.internal_auth import InternalAuditEvent, InternalUser
from database.session import get_database_identity, get_session, init_db


def main() -> int:
    parser = argparse.ArgumentParser(description="Set the shared internal-web password securely")
    parser.add_argument("--interactive", action="store_true", required=True)
    parser.parse_args()
    if os.name == "nt" and not ctypes.windll.shell32.IsUserAnAdmin():
        raise RuntimeError("ADMINISTRATOR_REQUIRED")
    _load_service_environment()
    settings = load_internal_web_settings()
    if not settings.shared_password_enabled:
        raise RuntimeError("LOCAL_SHARED_PASSWORD_MODE_REQUIRED")
    password = getpass.getpass("New shared password (minimum 16 characters): ")
    confirmation = getpass.getpass("Confirm shared password: ")
    try:
        if password != confirmation:
            raise RuntimeError("PASSWORD_CONFIRMATION_MISMATCH")
        if len(password) < 16 or password.lower() in {"password", "changeme", "temporary-password-123"}:
            raise RuntimeError("SHARED_PASSWORD_POLICY_FAILED")
        init_db()
        session = get_session()
        try:
            sync_internal_users(session, settings)
            user = session.scalar(select(InternalUser).where(InternalUser.user_key == "shared_internal_user"))
            if user is None:
                raise RuntimeError("SHARED_INTERNAL_USER_NOT_CONFIGURED")
            LocalPasswordAuthService(session, settings.session_hours).set_password(user.id, password, must_change=False)
            now = datetime.now(timezone.utc)
            session.add(InternalAuditEvent(
                access_email=user.email, internal_user_id=user.id, role=user.role,
                operation="SHARED_PASSWORD_ROTATED", entity_type="internal_user", entity_id=str(user.id),
                metadata_json={}, created_at=now,
            ))
            session.commit()
            print({"database_path": get_database_identity().get("absolute_path"), "user_key": "shared_internal_user",
                   "password_updated": True, "sessions_revoked": "ALL", "updated_at": now.isoformat()})
        finally:
            session.close()
    finally:
        password = ""
        confirmation = ""
    return 0


def _load_service_environment() -> None:
    path = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "AITraderAssistant" / "config" / "internal-web.env"
    load_dotenv(path, override=False)


if __name__ == "__main__":
    raise SystemExit(main())
