from __future__ import annotations

import os
from enum import StrEnum


class IFindTransportMode(StrEnum):
    AUTO = "AUTO"
    HTTP = "HTTP"
    SDK = "SDK"
    DISABLED = "DISABLED"


class IFindCredentialStatus(StrEnum):
    CONFIGURED = "CONFIGURED"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    ACCESS_TOKEN_ONLY_NO_REFRESH = "ACCESS_TOKEN_ONLY_NO_REFRESH"


def select_transport(requested: str | IFindTransportMode = IFindTransportMode.AUTO) -> tuple[IFindTransportMode, IFindCredentialStatus]:
    mode = IFindTransportMode(str(requested).upper())
    refresh = _configured("IFIND_REFRESH_TOKEN")
    access = _configured("IFIND_ACCESS_TOKEN")
    sdk = _configured("IFIND_USERNAME") and _configured("IFIND_PASSWORD")

    if mode == IFindTransportMode.DISABLED:
        return mode, IFindCredentialStatus.NOT_CONFIGURED
    if mode == IFindTransportMode.HTTP:
        if refresh:
            return mode, IFindCredentialStatus.CONFIGURED
        if access:
            return mode, IFindCredentialStatus.ACCESS_TOKEN_ONLY_NO_REFRESH
        return mode, IFindCredentialStatus.NOT_CONFIGURED
    if mode == IFindTransportMode.SDK:
        return mode, IFindCredentialStatus.CONFIGURED if sdk else IFindCredentialStatus.NOT_CONFIGURED
    if refresh:
        return IFindTransportMode.HTTP, IFindCredentialStatus.CONFIGURED
    if access:
        return IFindTransportMode.HTTP, IFindCredentialStatus.ACCESS_TOKEN_ONLY_NO_REFRESH
    if sdk:
        return IFindTransportMode.SDK, IFindCredentialStatus.CONFIGURED
    return IFindTransportMode.DISABLED, IFindCredentialStatus.NOT_CONFIGURED


def _configured(name: str) -> bool:
    return bool(os.getenv(name, "").strip())
