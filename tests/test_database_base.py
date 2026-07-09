import importlib

from database.base import Base


MODEL_MODULES = [
    "database.models.stock",
    "database.models.market",
    "database.models.finance",
    "database.models.factor",
    "database.models.news",
    "database.models.ai",
    "database.models.order_plan",
    "database.models.trading",
    "database.models.review",
    "database.models.memory",
    "database.models.system",
]

CORE_TABLES = {
    "stock_master",
    "stock_market_data",
    "news",
    "stock_factor_score",
    "ai_analysis_result",
    "stock_ai_score",
    "prediction_record",
    "decision_snapshot",
    "order_plan",
    "order_price_candidate",
    "trading_account",
    "position",
    "daily_review",
    "agent_memory_note",
    "llm_usage",
    "config_history",
    "system_config",
}


def test_base_and_model_modules_import() -> None:
    for module_name in MODEL_MODULES:
        importlib.import_module(module_name)

    assert Base.metadata is not None


def test_core_tables_are_registered() -> None:
    import database.models  # noqa: F401

    assert CORE_TABLES.issubset(set(Base.metadata.tables))
