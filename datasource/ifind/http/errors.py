from __future__ import annotations

from enum import StrEnum


class IFindHttpErrorCategory(StrEnum):
    REFRESH_TOKEN_NOT_CONFIGURED = "REFRESH_TOKEN_NOT_CONFIGURED"
    REFRESH_TOKEN_INVALID = "REFRESH_TOKEN_INVALID"
    ACCESS_TOKEN_INVALID = "ACCESS_TOKEN_INVALID"
    ACCESS_TOKEN_EXPIRED = "ACCESS_TOKEN_EXPIRED"
    DEVICE_LIMIT_EXCEEDED = "DEVICE_LIMIT_EXCEEDED"
    NOT_AUTHORIZED = "NOT_AUTHORIZED"
    ACCOUNT_EXPIRED = "ACCOUNT_EXPIRED"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    NETWORK_ERROR = "NETWORK_ERROR"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    RESPONSE_SCHEMA_ERROR = "RESPONSE_SCHEMA_ERROR"


class IFindHttpError(RuntimeError):
    def __init__(self, category: IFindHttpErrorCategory, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.category = category
        self.status_code = status_code


def classify_http_error(status_code: int | None, provider_code: object, message: str, *, authentication: bool) -> IFindHttpErrorCategory:
    text = f"{provider_code or ''} {message}".lower()
    if "device" in text or "设备" in text:
        return IFindHttpErrorCategory.DEVICE_LIMIT_EXCEEDED
    if "account" in text and "expir" in text or "账号过期" in text or "账户过期" in text:
        return IFindHttpErrorCategory.ACCOUNT_EXPIRED
    if status_code == 429 or "rate" in text or "频率" in text or "too many" in text:
        return IFindHttpErrorCategory.RATE_LIMITED
    if "expired" in text or "过期" in text:
        return IFindHttpErrorCategory.REFRESH_TOKEN_INVALID if authentication else IFindHttpErrorCategory.ACCESS_TOKEN_EXPIRED
    if status_code in {401, 403} or "token" in text and any(item in text for item in ("invalid", "无效", "错误")):
        return IFindHttpErrorCategory.REFRESH_TOKEN_INVALID if authentication else IFindHttpErrorCategory.ACCESS_TOKEN_INVALID
    if "permission" in text or "authorized" in text or "权限" in text or "授权" in text:
        return IFindHttpErrorCategory.NOT_AUTHORIZED
    if status_code is not None and status_code >= 500:
        return IFindHttpErrorCategory.PROVIDER_ERROR
    return IFindHttpErrorCategory.RESPONSE_SCHEMA_ERROR if status_code and status_code < 400 else IFindHttpErrorCategory.PROVIDER_ERROR
