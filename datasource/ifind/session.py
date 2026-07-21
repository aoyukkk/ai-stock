from __future__ import annotations

import importlib
import importlib.util
import inspect
import hashlib
import os
import platform
import re
import subprocess
import sys
import threading
import time
from importlib import metadata
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

from datasource.ifind.errors import classify_error, sanitize_error
from datasource.ifind.function_registry import discover_function_registry
from datasource.ifind.schemas import IFindErrorCategory, SDKEnvironmentAudit


class IFindSessionError(RuntimeError):
    def __init__(self, category: IFindErrorCategory, message: str) -> None:
        super().__init__(message)
        self.category = category


class IFindSessionManager:
    """Single-session SDK loader. Importing this module never logs in."""

    def __init__(
        self,
        config: dict[str, Any],
        *,
        module_loader: Callable[[str], ModuleType] | None = None,
        module_finder: Callable[[str], Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self._loader = module_loader or importlib.import_module
        self._finder = module_finder or importlib.util.find_spec
        self.module: ModuleType | None = None
        self.module_name: str | None = None
        self.logged_in = False
        self.call_count = 0
        self.success_count = 0
        self.failed_count = 0
        self._lock = threading.Lock()
        self._sleep = sleep
        self._clock = clock
        self._last_call_at: float | None = None
        self._hard_limit = min(50, int(config.get("hard_max_total_calls", 50)))
        self._configured_limit = min(self._hard_limit, int(config.get("max_total_calls", 30)))
        self._min_interval = max(1000, int(config.get("min_interval_ms", 1000))) / 1000

    def inspect_environment(self) -> SDKEnvironmentAudit:
        candidates = [str(item) for item in self.config.get("sdk_module_candidates") or [] if _safe_module_name(str(item))]
        module_name = next((name for name in candidates if self._finder(name) is not None), None)
        if module_name is None:
            return self._environment(status="SDK_NOT_INSTALLED", subprocess_status="SDK_NOT_INSTALLED")
        try:
            module = self._loader(module_name)
            self.module, self.module_name = module, module_name
            inventory = _distribution_inventory()
            functions = _public_functions(module)
            return self._environment(
                status="AVAILABLE",
                module_name=module_name,
                version=_module_version(module, module_name),
                location="<conda_env>/Lib/site-packages/...",
                distribution_name="iFinDAPI",
                package_record_sha256=inventory["record_sha256"],
                pyd_count=inventory["pyd_count"],
                dll_count=inventory["dll_count"],
                so_count=inventory["so_count"],
                pth_count=inventory["pth_count"],
                functions=functions,
                function_registry=discover_function_registry(module),
                subprocess_status=_subprocess_import(module_name),
            )
        except Exception as exc:
            category = classify_error(exc)
            status = "NATIVE_DEPENDENCY_MISSING" if category == IFindErrorCategory.NATIVE_DEPENDENCY_MISSING else "IMPORT_FAILED"
            return self._environment(status=status, module_name=module_name, subprocess_status=status, error=sanitize_error(exc, self._secret_values()))

    def login(self) -> str:
        if self.module is None:
            environment = self.inspect_environment()
            if environment.sdk_status != "AVAILABLE" or self.module is None:
                raise IFindSessionError(IFindErrorCategory.SDK_NOT_INSTALLED, "iFinD SDK is not installed")
        function_name = self.config.get("login_function")
        if not function_name or not hasattr(self.module, str(function_name)):
            raise IFindSessionError(IFindErrorCategory.UNSUPPORTED_BY_SDK, "Configured login function is unavailable")
        function = getattr(self.module, str(function_name))
        kwargs = self._login_arguments(function)
        try:
            result = self.invoke(function, **kwargs)
            if result is False or (isinstance(result, int) and result < 0):
                self.success_count -= 1
                self.failed_count += 1
                raise RuntimeError("login failed")
            self.logged_in = True
            return "LOGIN_SUCCESS"
        except IFindSessionError:
            raise
        except Exception as exc:
            raise IFindSessionError(classify_error(exc), sanitize_error(exc, self._secret_values())) from exc

    def close(self) -> None:
        if not self.logged_in or self.module is None:
            return
        function_name = self.config.get("logout_function")
        try:
            if function_name and hasattr(self.module, str(function_name)):
                self.invoke(getattr(self.module, str(function_name)))
        finally:
            self.logged_in = False

    def __enter__(self) -> "IFindSessionManager":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    def invoke(self, function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            if self.call_count >= self._configured_limit or self.call_count >= self._hard_limit:
                raise IFindSessionError(IFindErrorCategory.TRIAL_LIMIT, "CALL_LIMIT_REACHED")
            now = self._clock()
            if self._last_call_at is not None:
                remaining = self._min_interval - (now - self._last_call_at)
                if remaining > 0:
                    self._sleep(remaining)
            self.call_count += 1
            try:
                result = function(*args, **kwargs)
            except Exception:
                self.failed_count += 1
                raise
            else:
                self.success_count += 1
                return result
            finally:
                self._last_call_at = self._clock()

    def _login_arguments(self, function: Callable[..., Any]) -> dict[str, str]:
        mapping = dict(self.config.get("login_arguments") or {})
        signature = inspect.signature(function)
        kwargs: dict[str, str] = {}
        for parameter, env_name in mapping.items():
            if parameter not in signature.parameters:
                continue
            value = os.getenv(str(env_name), "").strip()
            if not value:
                raise IFindSessionError(IFindErrorCategory.NOT_CONFIGURED, "Required iFinD credential is not configured")
            kwargs[str(parameter)] = value
        required = [item.name for item in signature.parameters.values() if item.default is inspect.Parameter.empty and item.kind in {inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY}]
        if any(name not in kwargs for name in required):
            raise IFindSessionError(IFindErrorCategory.NOT_CONFIGURED, "Login argument mapping is incomplete")
        return kwargs

    def _environment(self, *, status: str, subprocess_status: str, module_name: str | None = None, distribution_name: str | None = None, version: str | None = None, location: str | None = None, package_record_sha256: str | None = None, pyd_count: int = 0, dll_count: int = 0, so_count: int = 0, pth_count: int = 0, functions: list[dict[str, str]] | None = None, function_registry=None, error: str | None = None) -> SDKEnvironmentAudit:
        spec_paths = [Path("packaging/pyinstaller_backend.spec"), Path("build/pyinstaller/ai_trader_backend.spec")]
        hidden_import = any(path.exists() and "ifind" in path.read_text(encoding="utf-8", errors="ignore").lower() for path in spec_paths)
        return SDKEnvironmentAudit(
            windows_version=platform.platform(), architecture=platform.architecture()[0], python_version=platform.python_version(),
            conda_environment=os.getenv("CONDA_DEFAULT_ENV", "UNKNOWN"), sdk_status=status, sdk_module=module_name,
            distribution_name=distribution_name, sdk_version=version, sdk_install_location=location,
            package_record_sha256=package_record_sha256, native_pyd_count=pyd_count, native_dll_count=dll_count,
            native_so_count=so_count, pth_count=pth_count, public_functions=functions or [],
            function_registry=function_registry or [], subprocess_import_status=subprocess_status,
            client_dependency="UNKNOWN", local_service_dependency="UNKNOWN", pure_http_mode="UNKNOWN",
            packaging_feasibility="UNKNOWN" if status != "AVAILABLE" else ("REQUIRES_NATIVE_RUNTIME" if dll_count or so_count else "UNKNOWN"),
            pyinstaller_hidden_import_present=hidden_import, sanitized_error=error,
        )

    @staticmethod
    def _secret_values() -> list[str]:
        return [os.getenv("IFIND_ACCESS_TOKEN", ""), os.getenv("IFIND_REFRESH_TOKEN", ""), os.getenv("IFIND_API_KEY", ""), os.getenv("IFIND_PASSWORD", "")]


def _safe_module_name(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", value))


def _public_functions(module: ModuleType) -> list[dict[str, str]]:
    rows = []
    for name in sorted(item for item in dir(module) if item.startswith("THS_") or item == "iFinD"):
        value = getattr(module, name)
        if callable(value):
            try:
                signature = str(inspect.signature(value))
            except (TypeError, ValueError):
                signature = "SIGNATURE_UNAVAILABLE"
            rows.append({"name": name[:128], "signature": signature[:300]})
    return rows[:200]


def _module_version(module: ModuleType, module_name: str) -> str | None:
    value = getattr(module, "__version__", None)
    if value:
        return str(value)[:64]
    try:
        return metadata.version("iFinDAPI")[:64]
    except metadata.PackageNotFoundError:
        try:
            return metadata.version(module_name)[:64]
        except metadata.PackageNotFoundError:
            return None


def _distribution_inventory() -> dict[str, Any]:
    result = {"pyd_count": 0, "dll_count": 0, "so_count": 0, "pth_count": 0, "record_sha256": None}
    try:
        distribution = metadata.distribution("iFinDAPI")
    except metadata.PackageNotFoundError:
        return result
    for item in distribution.files or []:
        suffix = Path(str(item)).suffix.lower()
        key = {".pyd": "pyd_count", ".dll": "dll_count", ".so": "so_count", ".pth": "pth_count"}.get(suffix)
        if key:
            result[key] += 1
    record = Path(distribution._path) / "RECORD"
    if record.exists():
        result["record_sha256"] = hashlib.sha256(record.read_bytes()).hexdigest()
    return result


def _subprocess_import(module_name: str) -> str:
    completed = subprocess.run(
        [sys.executable, "-c", f"import {module_name}"], capture_output=True, text=True, timeout=30, check=False,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    return "SUCCESS" if completed.returncode == 0 else "FAILED"
