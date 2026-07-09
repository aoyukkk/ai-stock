from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"


REQUIRED_CONFIG_FILES = [
    "system.yaml",
    "stock_scan.yaml",
    "schedule.yaml",
    "market_data.yaml",
    "event_trigger.yaml",
    "models.yaml",
    "agents.yaml",
    "ai_score.yaml",
    "llm.yaml",
    "token_cost.yaml",
    "risk_rules.yaml",
    "risk.yaml",
    "order_price.yaml",
    "memory.yaml",
    "paper_trading.yaml",
    "virtual_trading.yaml",
    "ui.yaml",
    "data_sources.yaml",
    "frontend.yaml",
]


def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as config_file:
        loaded = yaml.safe_load(config_file)

    assert isinstance(loaded, dict), f"{path.name} must parse to a mapping"
    return loaded


def test_required_config_files_exist() -> None:
    missing = [
        filename
        for filename in REQUIRED_CONFIG_FILES
        if not (CONFIG_DIR / filename).is_file()
    ]

    assert not missing, f"Missing required config files: {missing}"


def test_all_yaml_configs_parse() -> None:
    config_files = sorted(CONFIG_DIR.glob("*.yaml"))

    assert config_files, "Expected at least one YAML config file"

    for config_file in config_files:
        load_yaml(config_file)


def test_stock_scan_contains_q_l_n_r_w() -> None:
    stock_scan = load_yaml(CONFIG_DIR / "stock_scan.yaml")["stock_scan"]
    manual_watchlist = stock_scan["manual_watchlist"]

    assert stock_scan["quant_top_n"] == 500
    assert stock_scan["light_analysis_top_n"] == 500
    assert stock_scan["committee_analysis_top_n"] == 50
    assert stock_scan["final_recommend_top_n"] == 50
    assert manual_watchlist["default_count"] == 50
    assert manual_watchlist["soft_warning_count"] == 100
    assert manual_watchlist["hard_limit_enabled"] is False


def test_key_v03_config_skeletons_exist() -> None:
    system = load_yaml(CONFIG_DIR / "system.yaml")
    models = load_yaml(CONFIG_DIR / "models.yaml")
    risk_rules = load_yaml(CONFIG_DIR / "risk_rules.yaml")
    order_price = load_yaml(CONFIG_DIR / "order_price.yaml")
    memory = load_yaml(CONFIG_DIR / "memory.yaml")
    virtual_trading = load_yaml(CONFIG_DIR / "virtual_trading.yaml")
    schedule = load_yaml(CONFIG_DIR / "schedule.yaml")
    market_data = load_yaml(CONFIG_DIR / "market_data.yaml")
    event_trigger = load_yaml(CONFIG_DIR / "event_trigger.yaml")
    agents = load_yaml(CONFIG_DIR / "agents.yaml")
    ai_score = load_yaml(CONFIG_DIR / "ai_score.yaml")
    llm = load_yaml(CONFIG_DIR / "llm.yaml")
    token_cost = load_yaml(CONFIG_DIR / "token_cost.yaml")
    paper_trading = load_yaml(CONFIG_DIR / "paper_trading.yaml")
    risk = load_yaml(CONFIG_DIR / "risk.yaml")
    ui = load_yaml(CONFIG_DIR / "ui.yaml")
    frontend = load_yaml(CONFIG_DIR / "frontend.yaml")
    data_sources = load_yaml(CONFIG_DIR / "data_sources.yaml")

    assert system["configuration"]["priority"] == [
        "web_ui",
        "database",
        "config_file",
        "default",
    ]
    assert "refresh_frequency" in system
    assert models["llm_gateway"]["required"] is True
    assert "agent_routes" in models
    assert "event_trigger" in risk_rules
    assert "cancel_conditions" in order_price["order_price"]
    assert "reprice_conditions" in order_price["order_price"]
    assert "quality_control" in memory["memory"]
    assert virtual_trading["virtual_trading"]["rules"]["t_plus_one"] is True
    assert schedule["schedule"]["pre_market_recheck"]["enabled"] is True
    assert "trading_hours" in market_data["market_data"]
    assert event_trigger["event_trigger"]["order"]["reprice_threshold_percent"] == 1.5
    assert agents["agents"]["controller_agent"]["enabled"] is True
    assert ai_score["ai_score"]["risk_gate"]["enabled"] is True
    assert llm["llm_gateway"]["required"] is True
    assert llm["llm"]["mock_only"] is True
    assert token_cost["token"]["daily_budget"]["default"] == 1800000
    assert paper_trading["paper_trading"]["rules"]["t_plus_one"] is True
    assert risk["risk"]["position_limit_mode"] == "advisory_only"
    assert ui["ui"]["config_change"]["write_history"] is True
    assert "zh-CN" in frontend["frontend"]["supported_languages"]
    assert data_sources["data_sources"]["market_primary"]["provider"] == "tushare"
    assert data_sources["data_sources"]["market_primary"]["manual_only"] is True
    assert data_sources["data_sources"]["market_history"]["provider"] == "tushare"
    assert data_sources["data_sources"]["market_history"]["manual_only"] is True
    assert data_sources["data_sources"]["market_backup"][0]["provider"] == "baostock"
    assert data_sources["data_sources"]["tushare"]["token_env"] == "TUSHARE_TOKEN"
    assert data_sources["data_sources"]["akshare"]["enabled"] is False


def test_real_trading_defaults_disabled() -> None:
    system = load_yaml(CONFIG_DIR / "system.yaml")
    virtual_trading = load_yaml(CONFIG_DIR / "virtual_trading.yaml")
    paper_trading = load_yaml(CONFIG_DIR / "paper_trading.yaml")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert system["safety"]["enable_real_trading"] is False
    assert system["safety"]["ai_auto_real_order_enabled"] is False
    assert virtual_trading["virtual_trading"]["real_trading_enabled"] is False
    assert paper_trading["paper_trading"]["real_trading_enabled"] is False
    assert "ENABLE_REAL_TRADING=false" in env_example
    assert "AI_AUTO_REAL_ORDER_ENABLED=false" in env_example
