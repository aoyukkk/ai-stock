from fastapi import APIRouter

from backend.core.config import get_settings
from backend.schemas.response import success_response


router = APIRouter(tags=["health"])


@router.get("/health")
def health_check() -> dict:
    settings = get_settings()
    return success_response(
        data={
            "status": "ok",
            "service": settings.service_name,
            "version": settings.version,
            "environment": settings.app_env,
        },
        message="healthy",
    )
