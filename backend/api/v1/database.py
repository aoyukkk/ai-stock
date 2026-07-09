from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from backend.core.config import get_app_config
from backend.core.responses import success_response
from database.session import (
    create_engine_from_url,
    get_database_type,
    get_database_url,
)


router = APIRouter(prefix="/database", tags=["database"])


@router.get("/health")
def database_health(request: Request) -> dict:
    database_url = get_database_url()
    database_type = get_database_type(database_url)
    connected = False
    status = "unavailable"

    try:
        engine = create_engine_from_url(database_url)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        connected = True
        status = "ok"
    except SQLAlchemyError:
        connected = False
        status = "unavailable"
    finally:
        if "engine" in locals():
            engine.dispose()

    config = get_app_config()
    return success_response(
        data={
            "status": status,
            "database_configured": bool(database_url),
            "database_type": database_type,
            "connected": connected,
            "real_trading_enabled": config.real_trading_enabled,
        },
        trace_id=request.state.trace_id,
    )
