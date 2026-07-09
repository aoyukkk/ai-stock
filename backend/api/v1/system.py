from fastapi import APIRouter, Request

from backend.core.config import get_app_config
from backend.core.responses import success_response


router = APIRouter(prefix="/system", tags=["system"])


@router.get("/config-summary")
def config_summary(request: Request) -> dict:
    config = get_app_config()
    return success_response(
        data=config.safe_summary(),
        trace_id=request.state.trace_id,
    )
