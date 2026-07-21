from __future__ import annotations

import base64
import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path

from backend.core.runtime_paths import server_data_root


class DpapiUnavailable(RuntimeError):
    pass


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


class WindowsDpapiSecretStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = (path or server_data_root() / "secrets" / "application.dpapi.json").resolve()

    def status(self, providers: list[str]) -> dict[str, dict[str, bool]]:
        values = self._read()
        return {provider: {"configured": provider in values} for provider in providers}

    def set(self, provider: str, value: str) -> None:
        if not value:
            raise ValueError("SECRET_VALUE_REQUIRED")
        values = self._read()
        values[provider] = base64.b64encode(_protect(value.encode("utf-8"))).decode("ascii")
        self._write(values)

    def delete(self, provider: str) -> None:
        values = self._read()
        values.pop(provider, None)
        self._write(values)

    def load_into_environment(self, mapping: dict[str, str]) -> None:
        values = self._read()
        for provider, env_name in mapping.items():
            encrypted = values.get(provider)
            if encrypted:
                os.environ[env_name] = _unprotect(base64.b64decode(encrypted)).decode("utf-8")

    def _read(self) -> dict[str, str]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as exc:
            raise DpapiUnavailable("DPAPI_SECRET_FILE_INVALID") from exc
        return payload if isinstance(payload, dict) else {}

    def _write(self, values: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(values, sort_keys=True), encoding="utf-8")
        os.replace(temporary, self.path)


def _protect(value: bytes) -> bytes:
    return _crypt(value, protect=True)


def _unprotect(value: bytes) -> bytes:
    return _crypt(value, protect=False)


def _crypt(value: bytes, *, protect: bool) -> bytes:
    if os.name != "nt":
        raise DpapiUnavailable("WINDOWS_DPAPI_REQUIRED")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    source_buffer = ctypes.create_string_buffer(value)
    source = DATA_BLOB(len(value), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_byte)))
    output = DATA_BLOB()
    flags = 0x01
    if protect:
        success = crypt32.CryptProtectData(ctypes.byref(source), None, None, None, None, flags, ctypes.byref(output))
    else:
        success = crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, flags, ctypes.byref(output))
    if not success:
        raise DpapiUnavailable("WINDOWS_DPAPI_OPERATION_FAILED")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)
