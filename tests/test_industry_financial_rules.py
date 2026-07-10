from fundamentals.financial_status import FinancialStatusEngine


def test_banks_are_not_high_risk_from_generic_leverage_alone():
    engine = FinancialStatusEngine()
    for code in ("000001.SZ", "600000.SH"):
        result = engine.evaluate({"debt_to_assets": 92, "n_cashflow_act": -100}, structured_industry="银行", classification_source="Tushare industry")
        assert result.status == "INSUFFICIENT_DATA"
        assert result.rule_profile == "BANK"
        assert "debt_to_assets" in result.excluded_metrics
        assert "INDUSTRY_SPECIFIC_METRICS_MISSING" in result.reason_codes
        assert result.missing_industry_metrics


def test_general_manufacturer_keeps_generic_leverage_rule():
    result = FinancialStatusEngine().evaluate({"debt_to_assets": 90, "roe": 1, "or_yoy": -2}, structured_industry="专用设备", classification_source="Tushare industry")
    assert result.rule_profile == "GENERAL_CORPORATE"
    assert result.status == "HIGH_RISK"
