from __future__ import annotations

import os
from pathlib import Path


def cache_root() -> Path:
    return Path(os.getenv("AI_TRADER_CACHE_DIR", "data/cache")).expanduser().resolve()


def tushare_cache_root() -> Path:
    return cache_root() / "tushare"


def output_root() -> Path:
    return Path(os.getenv("AI_TRADER_OUTPUT_DIR", "outputs")).expanduser().resolve()


def report_root() -> Path:
    return Path(os.getenv("AI_TRADER_DIAGNOSTICS_DIR", "data/reports")).expanduser().resolve()

