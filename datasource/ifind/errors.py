from __future__ import annotations

import re
from typing import Iterable

from datasource.ifind.schemas import IFindErrorCategory


_RULES: tuple[tuple[IFindErrorCategory, tuple[str, ...]], ...] = (
    (IFindErrorCategory.SDK_NOT_INSTALLED, ("no module named", "module not found", "sdk not installed")),
    (IFindErrorCategory.NATIVE_DEPENDENCY_MISSING, ("dll load failed", "specified module could not be found", "native dependency")),
    (IFindErrorCategory.CLIENT_NOT_RUNNING, ("client not running", "terminal not running", "客户端未启动", "终端未启动")),
    (IFindErrorCategory.NOT_CONFIGURED, ("not configured", "credential", "未配置")),
    (IFindErrorCategory.ACCOUNT_EXPIRED, ("account expired", "账号过期", "账户过期")),
    (IFindErrorCategory.TRIAL_LIMIT, ("trial limit", "试用限制", "试用额度")),
    (IFindErrorCategory.QUOTA_EXHAUSTED, ("quota exhausted", "额度耗尽", "流量耗尽")),
    (IFindErrorCategory.RATE_LIMITED, ("rate limit", "too many requests", "频率限制", "请求过快")),
    (IFindErrorCategory.NOT_AUTHORIZED, ("not authorized", "permission denied", "无权限", "未授权")),
    (IFindErrorCategory.LOGIN_FAILED, ("login failed", "authentication failed", "登录失败", "认证失败")),
    (IFindErrorCategory.INVALID_PARAMETER, ("invalid parameter", "bad parameter", "参数错误", "参数无效")),
    (IFindErrorCategory.EMPTY_VALID_RESULT, ("empty valid", "valid empty", "有效空结果")),
    (IFindErrorCategory.TIMEOUT, ("timeout", "timed out", "超时")),
    (IFindErrorCategory.NETWORK_ERROR, ("network", "connection", "dns", "网络")),
    (IFindErrorCategory.RESPONSE_SCHEMA_ERROR, ("schema", "response format", "响应格式")),
    (IFindErrorCategory.UNSUPPORTED_BY_SDK, ("unsupported by sdk", "function not found", "sdk不支持")),
    (IFindErrorCategory.PROVIDER_ERROR, ("provider error", "server error", "服务端错误")),
)


def classify_error(error: BaseException | str) -> IFindErrorCategory:
    if isinstance(error, ModuleNotFoundError):
        return IFindErrorCategory.SDK_NOT_INSTALLED
    if isinstance(error, TimeoutError):
        return IFindErrorCategory.TIMEOUT
    text = str(error).lower()
    for category, markers in _RULES:
        if any(marker in text for marker in markers):
            return category
    return IFindErrorCategory.UNKNOWN_ERROR


def sanitize_error(error: BaseException | str, secrets: Iterable[str] = ()) -> str:
    text = str(error).replace("\r", " ").replace("\n", " ")[:500]
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"(?i)(access[_ -]?token|refresh[_ -]?token|api[_ -]?key|password|authorization)\s*[:=]\s*[^\s,;]+", r"\1=[REDACTED]", text)
    text = re.sub(r"[A-Za-z0-9+/=_-]{36,}\.[A-Za-z0-9+/=_-]{20,}(?:\.[A-Za-z0-9+/=_-]{20,})?", "[REDACTED_TOKEN]", text)
    return text or type(error).__name__
