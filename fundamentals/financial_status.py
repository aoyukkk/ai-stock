from __future__ import annotations

from typing import Any

from fundamentals.schemas import FinancialStatusResult


def _number(data: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
    return None


FINANCIAL_PROFILES = {
    "BANK": ("银行", "bank"),
    "INSURANCE": ("保险", "insurance"),
    "SECURITIES": ("证券", "券商", "securities"),
    "OTHER_FINANCIAL": ("金融", "financial"),
}


class FinancialStatusEngine:
    """Pure rule engine; no model can override its status."""

    def evaluate(
        self,
        data: dict[str, Any],
        *,
        structured_industry: str | None = None,
        classification_source: str = "UNKNOWN",
    ) -> FinancialStatusResult:
        rule_profile = self.classify(structured_industry)
        if rule_profile in {"BANK", "INSURANCE", "SECURITIES", "OTHER_FINANCIAL"}:
            return self._evaluate_financial_industry(data, rule_profile, classification_source)
        revenue_yoy = _number(data, "or_yoy", "revenue_yoy")
        profit_yoy = _number(data, "netprofit_yoy", "profit_yoy")
        roe = _number(data, "roe", "roe_dt")
        debt = _number(data, "debt_to_assets", "debt_ratio")
        operating_cash = _number(data, "n_cashflow_act", "operating_cash_flow")
        net_profit = _number(data, "n_income_attr_p", "net_profit")
        audit = str(data.get("audit_result") or "").lower()
        fields = [key for key in data if data.get(key) not in (None, "")]
        if len(fields) < 3:
            return self._result("INSUFFICIENT_DATA", ["INSUFFICIENT_DATA"], fields, data, rule_profile, classification_source)
        reasons: list[str] = []
        high_risk = False
        if debt is not None and debt >= 85:
            reasons.append("EXCESSIVE_LEVERAGE")
            high_risk = True
        if audit and not any(word in audit for word in ("standard", "unqualified", "标准无保留")):
            reasons.append("NON_STANDARD_AUDIT")
            high_risk = True
        if net_profit is not None and net_profit < 0:
            reasons.append("NET_LOSS")
        if operating_cash is not None and operating_cash < 0:
            reasons.append("NEGATIVE_OPERATING_CASH_FLOW")
        if profit_yoy is not None and profit_yoy < -30:
            reasons.append("PROFIT_SHARP_DECLINE")
        if high_risk:
            status = "HIGH_RISK"
        elif len(reasons) >= 2:
            status = "PRESSURED"
        elif reasons:
            status = "STABLE"
        elif (roe or 0) >= 10 and (revenue_yoy or 0) >= 0 and (profit_yoy or 0) >= 0:
            status = "HEALTHY"
        else:
            status = "STABLE"
        return self._result(status, reasons or ["NO_MAJOR_RULE_RISK"], fields, data, rule_profile, classification_source)

    @staticmethod
    def classify(industry: str | None) -> str:
        normalized = str(industry or "").lower()
        if not normalized:
            return "INSUFFICIENT_CLASSIFICATION"
        for profile, needles in FINANCIAL_PROFILES.items():
            if any(needle in normalized for needle in needles):
                return profile
        return "GENERAL_CORPORATE"

    def _evaluate_financial_industry(self, data, profile, source):
        required = {
            "BANK": ["capital_adequacy_ratio", "npl_ratio", "provision_coverage_ratio"],
            "INSURANCE": ["solvency_adequacy_ratio", "combined_ratio"],
            "SECURITIES": ["net_capital", "risk_coverage_ratio"],
            "OTHER_FINANCIAL": ["industry_specific_capital_ratio"],
        }[profile]
        missing = [key for key in required if data.get(key) in (None, "")]
        reasons = ["INDUSTRY_SPECIFIC_METRICS_MISSING"] if missing else ["INDUSTRY_METRICS_AVAILABLE"]
        audit = str(data.get("audit_result") or "").lower()
        status = "INSUFFICIENT_DATA" if missing else "STABLE"
        if audit and not any(word in audit for word in ("standard", "unqualified", "标准无保留")):
            status = "HIGH_RISK"; reasons.append("NON_STANDARD_AUDIT")
        result = self._result(status, reasons, list(data), data, profile, source)
        result.applicable_metrics = required
        result.excluded_metrics = ["debt_to_assets", "current_ratio", "operating_cash_flow_volatility"]
        result.missing_industry_metrics = missing
        return result

    @staticmethod
    def _result(status: str, reasons: list[str], fields: list[str], data: dict[str, Any], rule_profile: str, classification_source: str) -> FinancialStatusResult:
        return FinancialStatusResult(
            status=status,
            profitability_status="PRESSURED" if "NET_LOSS" in reasons else "STABLE",
            growth_status="PRESSURED" if "PROFIT_SHARP_DECLINE" in reasons else "STABLE",
            cash_flow_status="PRESSURED" if "NEGATIVE_OPERATING_CASH_FLOW" in reasons else "STABLE",
            leverage_status="HIGH_RISK" if "EXCESSIVE_LEVERAGE" in reasons else "STABLE",
            receivable_risk="UNKNOWN",
            inventory_risk="UNKNOWN",
            goodwill_risk="UNKNOWN",
            audit_risk="HIGH_RISK" if "NON_STANDARD_AUDIT" in reasons else "STABLE",
            shareholder_action_risk="UNKNOWN",
            reason_codes=reasons,
            evidence_fields=fields,
            as_of_period=str(data.get("end_date") or "") or None,
            available_at=data.get("available_at"),
            rule_profile=rule_profile,
            industry_classification_source=classification_source,
            applicable_metrics=["roe", "revenue_yoy", "net_profit_yoy", "debt_to_assets", "operating_cash_flow"],
            excluded_metrics=[],
            missing_industry_metrics=[],
        )
