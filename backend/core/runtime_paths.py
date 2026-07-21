from __future__ import annotations

import os
from pathlib import Path


def server_data_root() -> Path:
    configured = os.getenv("AI_TRADER_SERVER_DATA_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(os.getenv("PROGRAMDATA", r"C:\ProgramData")) / "AITraderAssistant"


def internal_server_mode() -> bool:
    return os.getenv("APP_RUNTIME_MODE", "").strip().upper() == "INTERNAL_WEB_SERVER"


def _runtime_dir(name: str, legacy: str) -> Path:
    if internal_server_mode():
        return (server_data_root() / name).resolve()
    return Path(legacy).expanduser().resolve()


def cache_root() -> Path:
    return Path(os.getenv("AI_TRADER_CACHE_DIR") or _runtime_dir("cache", "data/cache")).expanduser().resolve()


def tushare_cache_root() -> Path:
    return cache_root() / "tushare"


def output_root() -> Path:
    return Path(os.getenv("AI_TRADER_OUTPUT_DIR") or _runtime_dir("outputs", "outputs")).expanduser().resolve()


def report_root() -> Path:
    return Path(os.getenv("AI_TRADER_DIAGNOSTICS_DIR") or _runtime_dir("diagnostics", "data/reports")).expanduser().resolve()
