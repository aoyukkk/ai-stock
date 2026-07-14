from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import uvicorn

from backend.core.desktop import DesktopPaths, prepare_desktop_database


def main() -> int:
    os.environ.setdefault("AI_TRADER_ENV", "production")
    os.environ.setdefault("APP_ENV", "production")
    os.environ.setdefault("AI_TRADER_DESKTOP_MODE", "true")
    os.environ.setdefault("ENABLE_REAL_TRADING", "false")
    os.environ.setdefault("SCHEDULER_ENABLED", "false")
    os.environ.setdefault("PAPER_TRADING_ENABLED", "false")
    os.environ.setdefault("AUTO_ORDER_CREATION", "false")
    os.environ.setdefault("LLM_REAL_CALLS_ENABLED", "false")
    os.environ.setdefault("RUN_REAL_FUNDAMENTAL_RESEARCH", "false")

    port = _required_port()
    token = os.getenv("AI_TRADER_LOCAL_API_TOKEN", "")
    if len(token) < 32:
        raise RuntimeError("AI_TRADER_LOCAL_API_TOKEN_REQUIRED")

    paths = DesktopPaths.from_environment()
    paths.create()
    os.environ["AI_TRADER_LOG_DIR"] = str(paths.logs)
    os.environ["DATABASE_URL"] = f"sqlite:///{paths.database.as_posix()}"
    _configure_exception_log(paths.logs)
    prepare_desktop_database(paths, backup_retention=int(os.getenv("AI_TRADER_BACKUP_RETENTION", "10")))

    from backend.main import create_app

    app = create_app()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level=os.getenv("APP_LOG_LEVEL", "info").lower(),
        access_log=False,
        server_header=False,
    )
    server = uvicorn.Server(config)
    app.state.uvicorn_server = server
    server.run()
    return 0


def _required_port() -> int:
    try:
        port = int(os.getenv("AI_TRADER_PORT", "0"))
    except ValueError as exc:
        raise RuntimeError("AI_TRADER_PORT_INVALID") from exc
    if not 1024 <= port <= 65535:
        raise RuntimeError("AI_TRADER_PORT_INVALID")
    return port


def _configure_exception_log(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)

    def handle_exception(exc_type, exc_value, exc_traceback):
        logging.getLogger("desktop").critical(
            "Unhandled backend exception",
            exc_info=(exc_type, exc_value, exc_traceback),
        )

    sys.excepthook = handle_exception


if __name__ == "__main__":
    raise SystemExit(main())
