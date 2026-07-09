from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.datasource import router as legacy_datasource_router
from backend.api.manual_run import router as manual_run_router
from backend.api.router import api_router, api_v1_router
from backend.api.v1.health import router as health_router
from backend.core.config import AppConfig, get_app_config
from backend.core.exceptions import register_exception_handlers
from backend.core.logging import configure_logging
from backend.core.middleware import ApiContractEnvelopeMiddleware, TraceIdMiddleware


def create_app(config: AppConfig | None = None) -> FastAPI:
    app_config = config or get_app_config()
    configure_logging(app_config.log_level)

    app = FastAPI(
        title=app_config.app_name,
        version=app_config.version,
        description="A-share short-term AI trading assistant backend",
    )
    app.state.config = app_config

    app.add_middleware(TraceIdMiddleware)
    app.add_middleware(ApiContractEnvelopeMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_config.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(manual_run_router)
    app.include_router(legacy_datasource_router)
    app.include_router(api_router)
    app.include_router(api_v1_router)
    return app


app = create_app()
