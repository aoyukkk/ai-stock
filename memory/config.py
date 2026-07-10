from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from backend.core.config import get_app_config
from memory.exceptions import MemoryConfigError


DEFAULT_MEMORY_CONFIG: dict[str, Any] = {
    "enabled": True,
    "default_storage": "database",
    "short_term": {"enabled": True, "ttl_hours": 24, "storage": "database"},
    "mid_term": {"enabled": True, "ttl_days": 20, "storage": "database"},
    "long_term": {"enabled": True, "storage": "database"},
    "episodic": {"enabled": True, "storage": "database"},
    "reflection": {
        "enabled": True,
        "source": "daily_review",
        "use_mock_llm": True,
        "storage": "database",
    },
    "retrieval": {
        "top_k": 5,
        "default_top_k": 5,
        "min_quality_score": 50,
        "include_expired": False,
        "log_retrieval": True,
        "filter_expired": True,
        "filter_conflicted": True,
        "filter_low_quality": True,
        "write_retrieval_log": True,
    },
    "pollution_control": {
        "enabled": True,
        "block_conflict_status": ["CONFLICTED", "INVALIDATED"],
        "require_should_reuse": True,
    },
    "vector": {"enabled": False, "provider": "chroma", "storage": "chroma", "top_k": 5},
    "graph": {"enabled": False, "provider": "neo4j", "storage": "neo4j"},
    "temporal_kg": {"enabled": True, "storage": "database"},
    "quality_control": {
        "min_reuse_score": 50,
        "mark_conflict_not_reuse": True,
        "expire_low_quality_memory_days": 30,
        "selective_add_delete": True,
    },
}


@dataclass(frozen=True)
class MemoryConfig:
    raw: dict[str, Any]

    @property
    def enabled(self) -> bool:
        return bool(self.raw.get("enabled", True))

    @property
    def default_storage(self) -> str:
        return str(self.raw.get("default_storage", "database"))

    @property
    def short_term(self) -> dict[str, Any]:
        return self.raw.get("short_term", {})

    @property
    def mid_term(self) -> dict[str, Any]:
        return self.raw.get("mid_term", {})

    @property
    def retrieval(self) -> dict[str, Any]:
        return self.raw.get("retrieval", {})

    @property
    def pollution_control(self) -> dict[str, Any]:
        return self.raw.get("pollution_control", {})

    @property
    def reflection(self) -> dict[str, Any]:
        return self.raw.get("reflection", {})

    @property
    def vector(self) -> dict[str, Any]:
        return self.raw.get("vector", {})

    @property
    def graph(self) -> dict[str, Any]:
        return self.raw.get("graph", {})

    @property
    def top_k(self) -> int:
        return int(self.retrieval.get("top_k") or self.retrieval.get("default_top_k") or 5)

    @property
    def min_quality_score(self) -> Decimal:
        return Decimal(str(self.retrieval.get("min_quality_score", 50))).quantize(Decimal("0.0001"))

    @property
    def include_expired(self) -> bool:
        return bool(self.retrieval.get("include_expired", False))

    @property
    def log_retrieval(self) -> bool:
        return bool(self.retrieval.get("log_retrieval", self.retrieval.get("write_retrieval_log", True)))

    @property
    def blocked_conflict_statuses(self) -> set[str]:
        values = self.pollution_control.get("block_conflict_status", ["CONFLICTED", "INVALIDATED"])
        return {str(value).upper() for value in values}

    @property
    def require_should_reuse(self) -> bool:
        return bool(self.pollution_control.get("require_should_reuse", True))

    @property
    def use_mock_llm_for_reflection(self) -> bool:
        return bool(self.reflection.get("use_mock_llm", True))

    def validate(self) -> None:
        if self.default_storage != "database":
            raise MemoryConfigError("Phase 12 memory default_storage must be database.")
        if self.top_k <= 0:
            raise MemoryConfigError("memory.retrieval.top_k must be greater than 0.")
        if self.min_quality_score < 0 or self.min_quality_score > 100:
            raise MemoryConfigError("memory.retrieval.min_quality_score must be between 0 and 100.")
        if int(self.short_term.get("ttl_hours", 24)) <= 0:
            raise MemoryConfigError("memory.short_term.ttl_hours must be greater than 0.")
        if int(self.mid_term.get("ttl_days", 20)) <= 0:
            raise MemoryConfigError("memory.mid_term.ttl_days must be greater than 0.")

        models = get_app_config().config_files.get("models", {}).get("llm", {})
        if self.use_mock_llm_for_reflection and not bool(models.get("mock_only", True)):
            raise MemoryConfigError("Reflection memory requires LLM mock_only mode.")
        # A real provider may be registered for explicit gateway connectivity tests.
        # Reflection remains isolated because this module requires mock_only above.

    def summary(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "default_storage": self.default_storage,
            "short_term": {
                "enabled": bool(self.short_term.get("enabled", True)),
                "ttl_hours": int(self.short_term.get("ttl_hours", 24)),
            },
            "mid_term": {
                "enabled": bool(self.mid_term.get("enabled", True)),
                "ttl_days": int(self.mid_term.get("ttl_days", 20)),
            },
            "reflection": {
                "enabled": bool(self.reflection.get("enabled", True)),
                "source": self.reflection.get("source", "daily_review"),
                "use_mock_llm": self.use_mock_llm_for_reflection,
            },
            "retrieval": {
                "top_k": self.top_k,
                "min_quality_score": float(self.min_quality_score),
                "include_expired": self.include_expired,
                "log_retrieval": self.log_retrieval,
            },
            "pollution_control": {
                "enabled": bool(self.pollution_control.get("enabled", True)),
                "block_conflict_status": sorted(self.blocked_conflict_statuses),
                "require_should_reuse": self.require_should_reuse,
            },
            "vector": {
                "enabled": bool(self.vector.get("enabled", False)),
                "provider": self.vector.get("provider", "chroma"),
            },
            "graph": {
                "enabled": bool(self.graph.get("enabled", False)),
                "provider": self.graph.get("provider", "neo4j"),
            },
            "external_memory_backends_connected": False,
        }


def load_memory_config() -> MemoryConfig:
    raw = get_app_config().config_files.get("memory", {}).get("memory", {})
    merged = _deep_merge(DEFAULT_MEMORY_CONFIG, raw)
    config = MemoryConfig(raw=merged)
    config.validate()
    return config


def _deep_merge(default: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(default)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged
