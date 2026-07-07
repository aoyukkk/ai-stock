from fastapi import FastAPI

from backend.api.health import router as health_router
from backend.core.config import get_settings
from backend.core.exceptions import register_exception_handlers
from backend.core.logging import setup_logging


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level)

    app = FastAPI(
        title=settings.service_name,
        version=settings.version,
    )
    register_exception_handlers(app)
    app.include_router(health_router)
    return app


app = create_app()
