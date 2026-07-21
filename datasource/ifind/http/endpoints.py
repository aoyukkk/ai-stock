from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IFindHttpEndpoint:
    name: str
    path: str
    authentication: str


ENDPOINTS = {
    item.name: item
    for item in (
        IFindHttpEndpoint("get_access_token", "/get_access_token", "refresh_token"),
        IFindHttpEndpoint("real_time_quotation", "/real_time_quotation", "access_token"),
        IFindHttpEndpoint("high_frequency", "/high_frequency", "access_token"),
        IFindHttpEndpoint("cmd_history_quotation", "/cmd_history_quotation", "access_token"),
        IFindHttpEndpoint("basic_data_service", "/basic_data_service", "access_token"),
        IFindHttpEndpoint("date_sequence", "/date_sequence", "access_token"),
        IFindHttpEndpoint("data_pool", "/data_pool", "access_token"),
        IFindHttpEndpoint("report_query", "/report_query", "access_token"),
        IFindHttpEndpoint("edb_service", "/edb_service", "access_token"),
        IFindHttpEndpoint("snap_shot", "/snap_shot", "access_token"),
        IFindHttpEndpoint("get_trade_dates", "/get_trade_dates", "access_token"),
    )
}


def endpoint(name: str) -> IFindHttpEndpoint:
    try:
        return ENDPOINTS[name]
    except KeyError as exc:
        raise ValueError("IFIND_HTTP_ENDPOINT_NOT_REGISTERED") from exc
