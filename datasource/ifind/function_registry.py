from __future__ import annotations

import inspect
from types import ModuleType

from datasource.ifind.schemas import IFindSdkFunctionRegistry


FUNCTION_CANDIDATES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("authentication.login", ("THS_iFinDLogin",)),
    ("authentication.logout", ("THS_iFinDLogout",)),
    ("basic_data", ("THS_BD", "THS_BasicData")),
    ("history_quotes", ("THS_HQ", "THS_HistoryQuotes")),
    ("realtime_quotes", ("THS_RQ", "THS_RealtimeQuotes")),
    ("high_frequency_quotes", ("THS_HF", "THS_HighFrequenceSequence")),
    ("snapshot", ("THS_SS", "THS_Snapshot")),
    ("date_sequence", ("THS_DS", "THS_DateSerial")),
    ("data_pool", ("THS_DP", "THS_DataPool")),
    ("report_query", ("THS_ReportQuery",)),
    ("macro_edb", ("THS_EDB", "THS_EDBQuery")),
    ("trading_calendar", ("THS_Date_Query", "THS_DateQuery")),
    ("natural_language_query", ("THS_WC", "THS_WCQuery", "THS_iwencai")),
    ("research_data", ("THS_iResearch",)),
)


def discover_function_registry(module: ModuleType) -> list[IFindSdkFunctionRegistry]:
    registry = []
    for capability, candidates in FUNCTION_CANDIDATES:
        actual_name = next((name for name in candidates if callable(getattr(module, name, None))), None)
        function = getattr(module, actual_name) if actual_name else None
        registry.append(IFindSdkFunctionRegistry(
            canonical_capability=capability,
            actual_function_name=actual_name,
            callable=function is not None,
            signature_summary=_signature(function),
            source_module=module.__name__,
            available=function is not None,
        ))
    return registry


def _signature(function) -> str | None:
    if function is None:
        return None
    try:
        return str(inspect.signature(function))[:300]
    except (TypeError, ValueError):
        return "SIGNATURE_UNAVAILABLE"
