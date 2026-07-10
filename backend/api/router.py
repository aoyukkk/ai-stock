from fastapi import APIRouter

from backend.api.v1.alerts import router as alerts_router
from backend.api.v1.database import router as database_router
from backend.api.v1.committee import router as committee_router
from backend.api.v1.config import router as config_router
from backend.api.v1.data_sources import router as data_sources_router
from backend.api.v1.data_readiness import router as data_readiness_router
from backend.api.v1.health import router as health_router
from backend.api.v1.llm import router as llm_router
from backend.api.v1.fundamental_research import router as fundamental_research_router
from backend.api.v1.memory import router as memory_router
from backend.api.v1.models import router as models_router
from backend.api.v1.order_price import router as order_price_router
from backend.api.v1.position_sizing import router as position_sizing_router
from backend.api.v1.quant import router as quant_router
from backend.api.v1.recheck import router as recheck_router
from backend.api.v1.review import router as review_router
from backend.api.v1.screening import router as screening_router
from backend.api.v1.system import router as system_router
from backend.api.v1.virtual_trading import router as virtual_trading_router


api_v1_router = APIRouter(prefix="/api/v1")
api_router = APIRouter(prefix="/api")


def _include_business_routers(router: APIRouter) -> None:
    router.include_router(health_router)
    router.include_router(system_router)
    router.include_router(config_router)
    router.include_router(database_router)
    router.include_router(data_sources_router)
    router.include_router(data_readiness_router)
    router.include_router(quant_router)
    router.include_router(llm_router)
    router.include_router(fundamental_research_router)
    router.include_router(models_router)
    router.include_router(screening_router)
    router.include_router(committee_router)
    router.include_router(order_price_router)
    router.include_router(position_sizing_router)
    router.include_router(virtual_trading_router)
    router.include_router(recheck_router)
    router.include_router(alerts_router)
    router.include_router(review_router)
    router.include_router(memory_router)


_include_business_routers(api_router)
_include_business_routers(api_v1_router)
