from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import select


def _load_service_environment() -> None:
    root = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "AITraderAssistant"
    load_dotenv(root / "config" / "internal-web.env", override=False)


def set_shared_password() -> int:
    _load_service_environment()
    from backend.core.internal_auth import LocalPasswordAuthService, sync_internal_users
    from backend.core.internal_settings import load_internal_web_settings
    from database.models.internal_auth import InternalUser
    from database.session import get_session, init_db

    settings = load_internal_web_settings()
    if not settings.shared_password_enabled:
        raise RuntimeError("LOCAL_SHARED_PASSWORD_MODE_REQUIRED")
    password = getpass.getpass("请输入合伙人共享密码（至少 12 位）：")
    confirmation = getpass.getpass("请再次输入共享密码：")
    if password != confirmation:
        raise RuntimeError("PASSWORD_CONFIRMATION_MISMATCH")
    if len(password) < 12:
        raise RuntimeError("PASSWORD_TOO_SHORT")

    init_db()
    session = get_session()
    try:
        sync_internal_users(session, settings)
        user = session.scalar(select(InternalUser).where(InternalUser.email == settings.shared_identity_email))
        if user is None or not user.active:
            raise RuntimeError("LOCAL_SHARED_USER_NOT_CONFIGURED")
        LocalPasswordAuthService(session, settings.session_hours).set_password(
            user.id, password, must_change=False
        )
    finally:
        session.close()
        password = ""
        confirmation = ""
    print("共享登录密码已安全写入，未保存明文。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="AI Trader Internal Web 管理工具")
    parser.add_argument("command", choices=["set-shared-password"])
    args = parser.parse_args()
    return set_shared_password() if args.command == "set-shared-password" else 2


if __name__ == "__main__":
    raise SystemExit(main())
