from __future__ import annotations

from backend.core.config_manager import ConfigManager
from position_sizing.schemas import PositionSizingConfig


PREFIX = "position_sizing."


def load_position_sizing_config(manager: ConfigManager | None = None) -> PositionSizingConfig:
    values = (manager or ConfigManager()).get_effective_config()["values"]
    return PositionSizingConfig(
        conviction_power=values[PREFIX + "conviction_power"],
        portfolio_open_risk_percent=values[PREFIX + "portfolio_open_risk_percent"],
        deployable_capital_percent=values[PREFIX + "deployable_capital_percent"],
        cash_reserve_percent=values[PREFIX + "cash_reserve_percent"],
        max_single_stock_percent=values[PREFIX + "max_single_stock_percent"],
        max_industry_percent=values[PREFIX + "max_industry_percent"],
        max_chain_percent=values[PREFIX + "max_chain_percent"],
        max_liquidity_participation=values[PREFIX + "max_liquidity_participation"],
        default_lot_size=values[PREFIX + "default_lot_size"],
        max_stop_loss_percent=values["order_price.max_stop_loss_percent"],
        minimum_risk_reward=values["order_price.min_risk_reward"],
    )
