import pytest

from backend.core.config import load_app_config
from backend.core.config_manager import ConfigManager, ConfigManagerError
from database.base import Base
from database.session import create_engine_from_url, get_session


@pytest.fixture()
def config_manager():
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = get_session(engine)
    try:
        yield ConfigManager(app_config=load_app_config(), session=session)
    finally:
        session.close()
        engine.dispose()


def test_effective_config_reads_yaml_defaults(config_manager: ConfigManager) -> None:
    effective = config_manager.get_effective_config()

    assert effective["priority"] == ["Web UI", "Database", "Config File", "Default"]
    assert effective["real_trading_enabled"] is False
    assert effective["values"]["stock_scan.quant_top_n"] == 500
    assert effective["values"]["llm.mock_only"] is True
    assert effective["values"]["llm.default_provider"] == "mock"
    assert effective["values"]["llm.budgets.daily_token_budget"] == 5_000_000
    assert effective["values"]["paper_trading.real_trading_enabled"] is False


def test_llm_gateway_config_uses_config_manager_and_keeps_secrets_out(
    config_manager: ConfigManager,
) -> None:
    models = config_manager.get_llm_gateway_config()

    assert models["llm"]["mock_only"] is True
    assert models["llm"]["providers"]["deepseek"]["enabled"] is True
    assert models["llm"]["aliases"]["light-screening-default"]["provider"] == "deepseek"
    assert "api_key" not in models["llm"]["providers"]["deepseek"]


def test_database_override_has_priority_and_reset_restores_yaml(
    config_manager: ConfigManager,
) -> None:
    result = config_manager.set_config_value(
        "stock_scan.quant_top_n",
        300,
        user="tester",
        reason="unit override",
    )

    assert result["old_value"] == 500
    assert result["effective_value"] == 300
    assert config_manager.get_config_value("stock_scan.quant_top_n") == 300

    reset = config_manager.reset_config_value(
        "stock_scan.quant_top_n",
        user="tester",
        reason="unit reset",
    )

    assert reset["old_value"] == 300
    assert reset["effective_value"] == 500
    assert config_manager.get_config_value("stock_scan.quant_top_n") == 500


def test_non_whitelist_and_sensitive_keys_are_not_editable(
    config_manager: ConfigManager,
) -> None:
    for key in (
        "unknown.config",
        "OPENAI_API_KEY",
        "THS_API_PASSWORD",
        "paper_trading.real_trading_enabled",
        "broker.real_provider_credential",
    ):
        with pytest.raises(ConfigManagerError) as exc_info:
            config_manager.set_config_value(key, "unsafe", user="tester", reason="blocked")
        assert exc_info.value.code == "CONFIG_KEY_NOT_EDITABLE"


def test_quant_weight_sum_is_validated(config_manager: ConfigManager) -> None:
    with pytest.raises(ConfigManagerError) as exc_info:
        config_manager.set_config_value(
            "quant_factor.weights.technical",
            0.30,
            user="tester",
            reason="bad weight",
        )

    assert exc_info.value.code == "CONFIG_VALUE_INVALID"
    assert "sum to 1" in exc_info.value.message

    result = config_manager.set_config_values_bulk(
        [
            {"config_key": "quant_factor.weights.technical", "value": 0.30},
            {"config_key": "quant_factor.weights.capital", "value": 0.20},
            {"config_key": "quant_factor.weights.emotion", "value": 0.20},
            {"config_key": "quant_factor.weights.momentum", "value": 0.15},
            {"config_key": "quant_factor.weights.risk", "value": 0.15},
        ],
        user="tester",
        reason="balanced weight update",
    )

    assert len(result["items"]) == 5
    assert config_manager.get_config_value("quant_factor.weights.technical") == 0.30


def test_phase14_mock_and_disabled_memory_constraints(config_manager: ConfigManager) -> None:
    invalid_items = [
        ("llm.default_provider", "openai"),
        ("llm.default_model", "gpt-5.5"),
        ("memory.vector.enabled", True),
        ("memory.graph.enabled", True),
    ]

    for key, value in invalid_items:
        with pytest.raises(ConfigManagerError) as exc_info:
            config_manager.set_config_value(key, value, user="tester", reason="blocked")
        assert exc_info.value.code == "CONFIG_VALUE_INVALID"

    result = config_manager.set_config_value(
        "llm.mock_only", False, user="tester", reason="guarded real-call preparation"
    )
    assert result["effective_value"] is False


def test_config_history_is_written(config_manager: ConfigManager) -> None:
    config_manager.set_config_value(
        "order_price.atr_window",
        20,
        user="tester",
        reason="history check",
    )

    history = config_manager.list_config_history(config_key="order_price.atr_window")

    assert history
    assert history[0]["config_key"] == "order_price.atr_window"
    assert history[0]["old_value"] == 14
    assert history[0]["new_value"] == 20
    assert history[0]["user"] == "tester"


def test_position_sizing_partition_and_history_rollback(config_manager: ConfigManager) -> None:
    with pytest.raises(ConfigManagerError):
        config_manager.set_config_value(
            "position_sizing.deployable_capital_percent", 0.7, user="tester", reason="invalid"
        )

    config_manager.set_config_value(
        "position_sizing.conviction_power",
        3.0,
        user="tester",
        reason="curve test",
    )
    history = config_manager.list_config_history(
        config_key="position_sizing.conviction_power"
    )
    rollback = config_manager.rollback_config_history(history[0]["id"], user="tester")
    assert rollback["effective_value"] == 2.0
