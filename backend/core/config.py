from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from backend.version import APP_VERSION

from backend.core.security import sanitize_config


ROOT_DIR = Path(__file__).resolve().parents[2]
CONFIG_DIR = Path(os.getenv("AI_TRADER_CONFIG_DIR") or ROOT_DIR / "config").resolve()

REQUIRED_CONFIG_FILES = (
    "system.yaml",
    "stock_scan.yaml",
    "schedule.yaml",
    "market_data.yaml",
    "event_trigger.yaml",
    "models.yaml",
    "agents.yaml",
    "ai_score.yaml",
    "llm.yaml",
    "token_cost.yaml",
    "risk_rules.yaml",
    "risk.yaml",
    "order_price.yaml",
    "memory.yaml",
    "paper_trading.yaml",
    "virtual_trading.yaml",
    "quant_factor.yaml",
    "ui.yaml",
    "frontend.yaml",
    "fundamental_research.yaml",
    "position_sizing.yaml",
    "temporal.yaml",
    "reporting.yaml",
)
OPTIONAL_CONFIG_FILES = ("data_sources.yaml", "review.yaml", "market_review.yaml", "ifind_probe.yaml", "ifind_shadow.yaml", "post_close_action.yaml", "midday_recommendation.yaml", "intraday_monitor.yaml", "internal_web.yaml", "entry_timing.yaml", "candidate_threshold.yaml", "entry_timing_v2_1.yaml", "entry_timing_v2_2.yaml")


class ConfigLoadError(RuntimeError):
    """Raised when Phase 1 cannot safely load local configuration."""


@dataclass(frozen=True)
class AppConfig:
    root_dir: Path
    config_dir: Path
    env: dict[str, str]
    config_files: dict[str, dict[str, Any]]

    @property
    def system(self) -> dict[str, Any]:
        return self.config_files.get("system", {}).get("system", {})

    @property
    def safety(self) -> dict[str, Any]:
        return self.config_files.get("system", {}).get("safety", {})

    @property
    def app_name(self) -> str:
        return str(self.system.get("name") or "AI Trader Assistant")

    @property
    def version(self) -> str:
        return APP_VERSION

    @property
    def environment(self) -> str:
        return self.env.get("APP_ENV") or str(self.system.get("environment") or "development")

    @property
    def timezone(self) -> str:
        return self.env.get("APP_TIMEZONE") or str(self.system.get("timezone") or "Asia/Shanghai")

    @property
    def log_level(self) -> str:
        return self.env.get("APP_LOG_LEVEL") or str(self.system.get("log_level") or "INFO")

    @property
    def real_trading_enabled(self) -> bool:
        env_value = self.env.get("ENABLE_REAL_TRADING")
        if env_value is not None:
            return _as_bool(env_value, default=False)

        virtual = self.config_files.get("virtual_trading", {}).get("virtual_trading", {})
        return bool(
            self.safety.get("enable_real_trading", False)
            or virtual.get("real_trading_enabled", False)
        )

    @property
    def cors_allowed_origins(self) -> list[str]:
        if self.env.get("AI_TRADER_DESKTOP_MODE", "false").lower() in {"1", "true", "yes", "on"}:
            return ["null"]
        frontend = self.env.get("FRONTEND_DEV_SERVER") or "http://localhost:5173"
        return [
            frontend,
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]

    def safe_summary(self) -> dict[str, Any]:
        stock_scan = self.config_files.get("stock_scan", {}).get("stock_scan", {})
        manual_watchlist = stock_scan.get("manual_watchlist", {})
        models = self.config_files.get("models", {})
        llm = models.get("llm", {})
        virtual = self.config_files.get("virtual_trading", {}).get("virtual_trading", {})
        memory = self.config_files.get("memory", {}).get("memory", {})

        data_sources_summary = _summarize_data_sources(
            self.config_files.get("data_sources", {}).get("data_sources", {})
        )
        llm_summary = _summarize_llm(llm)

        hard_limit_enabled = bool(manual_watchlist.get("hard_limit_enabled", False))
        manual_watchlist_limit = (
            manual_watchlist.get("soft_warning_count")
            if hard_limit_enabled
            else None
        )

        summary = {
            "system": {
                "name": self.app_name,
                "environment": self.environment,
                "timezone": self.timezone,
                "version": self.version,
                "real_trading_enabled": self.real_trading_enabled,
            },
            "config_priority": _display_config_priority(
                self.config_files.get("system", {})
                .get("configuration", {})
                .get("priority", [])
            ),
            "stock_scan": {
                "quant_top_n": stock_scan.get("quant_top_n"),
                "light_analysis_top_n": stock_scan.get("light_analysis_top_n"),
                "committee_analysis_top_n": stock_scan.get("committee_analysis_top_n"),
                "final_recommend_top_n": stock_scan.get("final_recommend_top_n"),
                "manual_watchlist_default": manual_watchlist.get("default_count"),
                "manual_watchlist_limit": manual_watchlist_limit,
                "manual_watchlist_soft_warning": manual_watchlist.get("soft_warning_count"),
            },
            "llm": llm_summary,
            "data_sources": data_sources_summary,
            "virtual_trading": {
                "enabled": bool(virtual.get("enabled", False)),
                "real_trading_enabled": bool(virtual.get("real_trading_enabled", False)),
            },
            "memory": {
                "short_term_enabled": _nested_enabled(memory, "short_term"),
                "mid_term_enabled": _nested_enabled(memory, "mid_term"),
                "vector_enabled": _nested_enabled(memory, "vector"),
                "long_term_enabled": _nested_enabled(memory, "long_term"),
                "reflection_enabled": _nested_enabled(memory, "reflection"),
                "temporal_kg_enabled": _nested_enabled(memory, "temporal_kg"),
                "graph_enabled": _nested_enabled(memory, "graph"),
            },
        }
        return sanitize_config(summary)


@lru_cache(maxsize=1)
def get_app_config() -> AppConfig:
    return load_app_config()


def load_app_config() -> AppConfig:
    load_dotenv(ROOT_DIR / ".env", override=False)
    env = dict(os.environ)
    config_files = _load_config_files(CONFIG_DIR)
    return AppConfig(
        root_dir=ROOT_DIR,
        config_dir=CONFIG_DIR,
        env=env,
        config_files=config_files,
    )


def _load_config_files(config_dir: Path) -> dict[str, dict[str, Any]]:
    loaded: dict[str, dict[str, Any]] = {}

    for filename in REQUIRED_CONFIG_FILES:
        path = config_dir / filename
        if not path.exists():
            raise ConfigLoadError(f"Missing required config file: {path}")
        loaded[path.stem] = _read_yaml_mapping(path)

    for filename in OPTIONAL_CONFIG_FILES:
        path = config_dir / filename
        if path.exists():
            loaded[path.stem] = _read_yaml_mapping(path)
        else:
            loaded[path.stem] = {}

    return loaded


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as config_file:
            data = yaml.safe_load(config_file) or {}
    except yaml.YAMLError as exc:
        raise ConfigLoadError(f"Invalid YAML in {path}") from exc

    if not isinstance(data, dict):
        raise ConfigLoadError(f"Config file must contain a YAML mapping: {path}")
    return data


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _normalize_version(value: Any) -> str:
    text = str(value)
    if text.count(".") == 1:
        return f"{text}.0"
    return text


def _display_config_priority(priority: list[Any]) -> list[str]:
    labels = {
        "web_ui": "Web UI",
        "database": "Database",
        "config_file": "Config File",
        "default": "Default",
    }
    return [labels.get(str(item), str(item)) for item in priority]


def _nested_enabled(config: dict[str, Any], key: str) -> bool:
    value = config.get(key, {})
    return bool(value.get("enabled", False)) if isinstance(value, dict) else False


def _summarize_llm(llm: dict[str, Any]) -> dict[str, Any]:
    providers = llm.get("providers", {})
    real_providers_enabled = [
        name
        for name, provider_config in providers.items()
        if name != "mock"
        and isinstance(provider_config, dict)
        and bool(provider_config.get("enabled", False))
    ]
    mock_config = providers.get("mock", {})
    return {
        "mode": "mock_only",
        "mock_enabled": bool(mock_config.get("enabled", False))
        if isinstance(mock_config, dict)
        else False,
        "real_providers_enabled": real_providers_enabled,
    }


def _summarize_data_sources(data_sources: dict[str, Any]) -> dict[str, Any]:
    providers = list(_iter_provider_configs(data_sources))
    real_sources_enabled = sorted({
        provider
        for provider, enabled, _manual_only in providers
        if provider != "mock" and enabled
    })
    manual_debug_sources = sorted({
        provider
        for provider, enabled, manual_only in providers
        if provider != "mock" and enabled and manual_only
    })
    mock_enabled = any(provider == "mock" and enabled for provider, enabled, _ in providers)
    return {
        "mode": data_sources.get("mode") or data_sources.get("phase0_mode", "mock_only"),
        "mock_enabled": mock_enabled,
        "real_sources_enabled": real_sources_enabled,
        "manual_debug_sources": manual_debug_sources,
        "enabled_data_sources": (["mock"] if mock_enabled else []) + real_sources_enabled,
    }


def _iter_provider_configs(obj: Any):
    if isinstance(obj, dict):
        manual_only = bool(obj.get("manual_only", False))
        if "provider" in obj:
            yield str(obj.get("provider")), bool(obj.get("enabled", False)), manual_only
        elif any(key in obj for key in ("akshare", "baostock", "ifind", "tushare")):
            for provider_name in ("akshare", "baostock", "ifind", "tushare"):
                provider_value = obj.get(provider_name)
                if isinstance(provider_value, dict) and "enabled" in provider_value:
                    yield (
                        provider_name,
                        bool(provider_value.get("enabled", False)),
                        bool(provider_value.get("manual_only", False)),
                    )
        for value in obj.values():
            yield from _iter_provider_configs(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_provider_configs(item)
