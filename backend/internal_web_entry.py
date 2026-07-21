from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# The service configuration must be loaded before importing modules that cache
# environment-derived paths or application configuration.
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
_DEFAULT_DATA_ROOT = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "AITraderAssistant"
_BOOT_DATA_ROOT = Path(os.environ.get("AI_TRADER_SERVER_DATA_ROOT", _DEFAULT_DATA_ROOT))
_BOOT_ENV_LOAD_ERROR: OSError | None = None
try:
    load_dotenv(_BOOT_DATA_ROOT / "config" / "internal-web.env", override=False)
except OSError as exc:
    # Importing the application factory must remain side-effect safe for tests
    # and tooling.  The executable entry point still fails closed below.
    _BOOT_ENV_LOAD_ERROR = exc

from backend.core.internal_auth import sync_internal_users
from backend.core.dpapi_secret_store import WindowsDpapiSecretStore
from backend.core.internal_settings import InternalWebSettings, load_internal_web_settings
from backend.core.process_lock import ProcessFileLock
from backend.core.runtime_paths import server_data_root
from backend.main import create_app
from database.session import get_session, init_db
from backend.api.runtime import SECRET_ENV


FRONTEND_DIST = ROOT / "frontend" / "dist"
PROTECTED_PREFIXES = ("api", "ws", "health", "metrics", "docs", "redoc", "openapi.json")
LOGGER = logging.getLogger(__name__)


def create_internal_web_app(
    settings: InternalWebSettings | None = None,
    *,
    frontend_dist: Path | None = None,
    lock_path: Path | None = None,
    auth_verifier=None,
):
    settings = settings or load_internal_web_settings()
    dist = (frontend_dist or FRONTEND_DIST).resolve()
    lock = ProcessFileLock(lock_path or server_data_root() / "temp" / "internal_web_server.lock")

    @asynccontextmanager
    async def lifespan(_app):
        settings.validate_production()
        if not (dist / "index.html").is_file():
            raise RuntimeError("FRONTEND_DIST_NOT_BUILT")
        _prepare_server_directories()
        lock.acquire()
        try:
            init_db()
            WindowsDpapiSecretStore().load_into_environment(SECRET_ENV)
            session = get_session()
            try:
                sync_internal_users(session, settings)
            finally:
                session.close()
            yield
        finally:
            lock.release()

    app = create_app(internal_settings=settings, auth_verifier=auth_verifier)
    app.router.lifespan_context = lifespan
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="internal-assets")

    @app.get("/{frontend_path:path}", include_in_schema=False)
    async def frontend(frontend_path: str):
        first = frontend_path.split("/", 1)[0].lower()
        if first in PROTECTED_PREFIXES:
            raise HTTPException(status_code=404, detail="NOT_FOUND")
        candidate = (dist / frontend_path).resolve()
        if frontend_path and candidate.is_file() and dist in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(dist / "index.html")

    return app


def run() -> None:
    if _BOOT_ENV_LOAD_ERROR is not None:
        raise RuntimeError("INTERNAL_WEB_ENV_UNREADABLE") from _BOOT_ENV_LOAD_ERROR
    settings = load_internal_web_settings()
    settings.validate_production()
    uvicorn.run(
        create_internal_web_app(settings),
        host="127.0.0.1",
        port=settings.origin_port,
        workers=1,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
        access_log=True,
    )


def _prepare_server_directories() -> None:
    root = server_data_root()
    for name in ("data", "cache", "outputs", "logs", "backups", "config", "secrets", "diagnostics", "temp"):
        (root / name).mkdir(parents=True, exist_ok=True)


app = create_internal_web_app()


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "set-shared-password":
        from backend.internal_web_admin import set_shared_password

        raise SystemExit(set_shared_password())
    run()
