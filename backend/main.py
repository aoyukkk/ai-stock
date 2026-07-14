from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.datasource import router as legacy_datasource_router
from backend.api.manual_run import router as manual_run_router
from backend.api.performance import router as performance_router
from backend.api.runtime import router as runtime_router, version_router
from backend.api.real_quant import router as real_quant_router
from backend.api.real_screening import router as real_screening_router
from backend.api.workbench import router as workbench_router
from backend.api.router import api_router, api_v1_router
from backend.api.v1.health import router as health_router
from backend.core.config import AppConfig, get_app_config
from backend.core.exceptions import register_exception_handlers
from backend.core.logging import configure_logging
from backend.core.middleware import ApiContractEnvelopeMiddleware, TraceIdMiddleware
from backend.core.local_auth import DesktopLocalAuthMiddleware
from database.session import assert_database_path_consistency, get_database_url


def create_app(config: AppConfig | None = None) -> FastAPI:
    app_config = config or get_app_config()
    configure_logging(app_config.log_level)

    desktop_mode = app_config.env.get("AI_TRADER_DESKTOP_MODE", "false").lower() in {"1", "true", "yes", "on"}
    app = FastAPI(
        title=app_config.app_name,
        version=app_config.version,
        description="A-share short-term AI trading assistant backend",
        docs_url=None if desktop_mode else "/docs",
        redoc_url=None if desktop_mode else "/redoc",
        openapi_url=None if desktop_mode else "/openapi.json",
    )
    app.state.config = app_config
    app.state.database_identity = assert_database_path_consistency(get_database_url(), get_database_url())

    app.add_middleware(TraceIdMiddleware)
    app.add_middleware(DesktopLocalAuthMiddleware)
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
    app.include_router(real_quant_router)
    app.include_router(real_screening_router)
    app.include_router(performance_router)
    app.include_router(runtime_router)
    app.include_router(version_router)
    app.include_router(workbench_router)
    app.include_router(api_router)
    app.include_router(api_v1_router)
    return app


app = create_app()
