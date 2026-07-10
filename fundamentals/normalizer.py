from __future__ import annotations

from typing import Any


FIELD_MAP = {
    "revenue": ("revenue", "total_revenue"),
    "operating_cost": ("oper_cost", "total_cogs"),
    "net_profit": ("n_income_attr_p", "netprofit"),
    "deducted_net_profit": ("profit_dedt", "dedt_np"),
    "operating_profit": ("operate_profit",),
    "rd_expense": ("rd_exp",),
    "selling_expense": ("sell_exp",),
    "admin_expense": ("admin_exp",),
    "finance_expense": ("fin_exp",),
    "gross_margin": ("grossprofit_margin",),
    "net_margin": ("netprofit_margin",),
    "roe": ("roe", "roe_dt"),
    "roa": ("roa",),
    "revenue_yoy": ("or_yoy", "tr_yoy"),
    "net_profit_yoy": ("netprofit_yoy",),
    "deducted_profit_yoy": ("dt_netprofit_yoy",),
    "total_assets": ("total_assets",),
    "total_liabilities": ("total_liab",),
    "debt_ratio": ("debt_to_assets",),
    "cash": ("money_cap",),
    "trading_financial_assets": ("trad_asset",),
    "accounts_receivable": ("accounts_receiv",),
    "inventory": ("inventories",),
    "goodwill": ("goodwill",),
    "short_term_borrowing": ("st_borr",),
    "long_term_borrowing": ("lt_borr",),
    "operating_cash_flow": ("n_cashflow_act",),
    "investing_cash_flow": ("n_cashflow_inv_act",),
    "financing_cash_flow": ("n_cash_flows_fnc_act",),
    "operating_cash_flow_per_share": ("ocfps",),
    "pe": ("pe", "pe_ttm"),
    "pb": ("pb",),
    "ps": ("ps", "ps_ttm"),
    "total_market_value": ("total_mv",),
    "circulating_market_value": ("circ_mv",),
    "turnover_rate": ("turnover_rate",),
}


def normalize_financial_snapshot(*rows: dict[str, Any]) -> dict[str, Any]:
    combined: dict[str, Any] = {}
    for row in rows:
        combined.update({key: value for key, value in row.items() if value not in (None, "")})
    normalized: dict[str, Any] = {}
    for target, candidates in FIELD_MAP.items():
        for source in candidates:
            if source in combined:
                normalized[target] = combined[source]
                break
    for key in ("ts_code", "end_date", "ann_date", "f_ann_date", "available_at", "update_flag", "report_type"):
        if key in combined:
            normalized[key] = combined[key]
    operating_cash = normalized.get("operating_cash_flow")
    net_profit = normalized.get("net_profit")
    try:
        normalized["cash_profit_quality"] = float(operating_cash) / abs(float(net_profit)) if float(net_profit) else None
    except (TypeError, ValueError):
        normalized["cash_profit_quality"] = None
    return normalized
