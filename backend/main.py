from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.datasource import router as legacy_datasource_router
from backend.api.manual_run import router as manual_run_router
from backend.api.performance import router as performance_router
from backend.api.runtime import router as runtime_router, version_router
from backend.api.real_quant import router as real_quant_router
from backend.api.real_screening import router as real_screening_router
from backend.api.workbench import router as workbench_router
from backend.api.market_review import router as market_review_router
from backend.api.ifind import router as ifind_router
from backend.api.realtime_monitor import router as realtime_monitor_router
from backend.api.post_close_actions import router as post_close_actions_router
from backend.api.midday import router as midday_router
from backend.api.intraday_monitor import router as intraday_monitor_router
from backend.api.entry_timing import router as entry_timing_router
from backend.api.router import api_router, api_v1_router
from backend.api.v1.health import router as health_router
from backend.core.config import AppConfig, get_app_config
from backend.core.exceptions import register_exception_handlers
from backend.core.logging import configure_logging
from backend.core.middleware import ApiContractEnvelopeMiddleware, TraceIdMiddleware
from backend.core.local_auth import DesktopLocalAuthMiddleware
from backend.core.internal_settings import InternalWebSettings, load_internal_web_settings
from backend.core.internal_middleware import CloudflareAccessAuthMiddleware, RequestBodyLimitMiddleware
from database.session import assert_database_path_consistency, get_database_url


def create_app(
    config: AppConfig | None = None,
    *,
    internal_settings: InternalWebSettings | None = None,
    auth_verifier=None,
) -> FastAPI:
    app_config = config or get_app_config()
    server_settings = internal_settings or load_internal_web_settings()
    configure_logging(app_config.log_level)

    desktop_mode = app_config.env.get("AI_TRADER_DESKTOP_MODE", "false").lower() in {"1", "true", "yes", "on"}
    protected_mode = desktop_mode or server_settings.enabled
    app = FastAPI(
        title=app_config.app_name,
        version=app_config.version,
        description="A-share short-term AI trading assistant backend",
        docs_url=None if protected_mode else "/docs",
        redoc_url=None if protected_mode else "/redoc",
        openapi_url=None if protected_mode else "/openapi.json",
    )
    app.state.config = app_config
    app.state.internal_settings = server_settings
    app.state.database_identity = assert_database_path_consistency(get_database_url(), get_database_url())

    app.add_middleware(TraceIdMiddleware)
    app.add_middleware(DesktopLocalAuthMiddleware)
    app.add_middleware(ApiContractEnvelopeMiddleware)
    if not server_settings.enabled:
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
    app.include_router(market_review_router)
    app.include_router(ifind_router)
    app.include_router(realtime_monitor_router)
    app.include_router(post_close_actions_router)
    app.include_router(midday_router)
    app.include_router(intraday_monitor_router)
    app.include_router(entry_timing_router)
    app.include_router(api_router)
    app.include_router(api_v1_router)
    if server_settings.enabled:
        from backend.api.internal_auth import router as internal_auth_router

        app.include_router(internal_auth_router)
        app.add_middleware(RequestBodyLimitMiddleware, max_bytes=server_settings.max_request_bytes)
        app.add_middleware(CloudflareAccessAuthMiddleware, settings=server_settings, verifier=auth_verifier)
    return app


app = create_app()
