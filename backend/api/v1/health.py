from fastapi import APIRouter, Request

from backend.core.config import get_app_config
from backend.core.responses import success_response


router = APIRouter(tags=["health"])


@router.get("/health")
def health(request: Request) -> dict:
    config = get_app_config()
    return success_response(
        data={
            "status": "ok",
            "service": config.app_name,
            "version": config.version,
            "environment": config.environment,
            "real_trading_enabled": config.real_trading_enabled,
            "external_services_connected": False,
            "external_services_mode": "mock_only",
            "desktop_mode": config.env.get("AI_TRADER_DESKTOP_MODE", "false").lower() in {"1", "true", "yes", "on"},
        },
        trace_id=request.state.trace_id,
    )
