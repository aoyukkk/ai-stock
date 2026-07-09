from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from backend.core.config import get_app_config
from agents.exceptions import AgentConfigError


DEFAULT_AGENT_CONFIGS = {
    "technical_agent": {"enabled": True, "provider": "mock", "model": "mock-chat", "weight": 0.20},
    "news_agent": {"enabled": True, "provider": "mock", "model": "mock-chat", "weight": 0.20},
    "capital_agent": {"enabled": True, "provider": "mock", "model": "mock-chat", "weight": 0.20},
    "emotion_agent": {"enabled": True, "provider": "mock", "model": "mock-chat", "weight": 0.15},
    "overseas_agent": {"enabled": True, "provider": "mock", "model": "mock-chat", "weight": 0.10},
    "risk_agent": {"enabled": True, "provider": "mock", "model": "mock-chat", "weight": 0.15},
}

DEFAULT_AI_COMMITTEE_CONFIG = {
    "enabled": True,
    "input_top_n": 50,
    "final_top_n": 50,
    "persist_default": False,
    "batch_size": 10,
    "agents": DEFAULT_AGENT_CONFIGS,
    "recommendation_thresholds": {
        "strong_watch": 85,
        "watch": 75,
        "neutral": 60,
    },
}


@dataclass(frozen=True)
class CommitteeAgentConfig:
    name: str
    raw: dict[str, Any]
    mock_only: bool = True

    @property
    def enabled(self) -> bool:
        return bool(self.raw.get("enabled", True))

    @property
    def provider(self) -> str:
        if self.mock_only:
            return "mock"
        return str(self.raw.get("provider", "mock"))

    @property
    def model(self) -> str:
        return str(self.raw.get("model", "mock-chat"))

    @property
    def weight(self) -> Decimal:
        return Decimal(str(self.raw.get("weight", DEFAULT_AGENT_CONFIGS[self.name]["weight"])))


@dataclass(frozen=True)
class AICommitteeConfig:
    raw: dict[str, Any]
    mock_only: bool = True

    @property
    def enabled(self) -> bool:
        return bool(self.raw.get("enabled", True))

    @property
    def input_top_n(self) -> int:
        return int(self.raw.get("input_top_n", 50))

    @property
    def final_top_n(self) -> int:
        return int(self.raw.get("final_top_n", 50))

    @property
    def persist_default(self) -> bool:
        return bool(self.raw.get("persist_default", False))

    @property
    def batch_size(self) -> int:
        return int(self.raw.get("batch_size", 10))

    @property
    def agents(self) -> dict[str, CommitteeAgentConfig]:
        raw_agents = self.raw.get("agents", {})
        return {
            name: CommitteeAgentConfig(
                name=name,
                raw=DEFAULT_AGENT_CONFIGS[name] | raw_agents.get(name, {}),
                mock_only=self.mock_only,
            )
            for name in DEFAULT_AGENT_CONFIGS
        }

    @property
    def enabled_agents(self) -> dict[str, CommitteeAgentConfig]:
        return {name: config for name, config in self.agents.items() if config.enabled}

    @property
    def score_weights(self) -> dict[str, Decimal]:
        return {name: config.weight for name, config in self.enabled_agents.items()}

    @property
    def recommendation_thresholds(self) -> dict[str, Decimal]:
        raw_thresholds = self.raw.get("recommendation_thresholds", {})
        defaults = DEFAULT_AI_COMMITTEE_CONFIG["recommendation_thresholds"]
        return {
            "strong_watch": Decimal(str(raw_thresholds.get("strong_watch", defaults["strong_watch"]))),
            "watch": Decimal(str(raw_thresholds.get("watch", defaults["watch"]))),
            "neutral": Decimal(str(raw_thresholds.get("neutral", defaults["neutral"]))),
        }

    def validate(self) -> None:
        if not self.enabled_agents:
            raise AgentConfigError("AI committee requires at least one enabled agent.")
        total = sum(self.score_weights.values(), Decimal("0"))
        if abs(total - Decimal("1")) > Decimal("0.0001"):
            raise AgentConfigError(f"AI committee agent weights must sum to 1.0, got {total}.")
        thresholds = self.recommendation_thresholds
        if not (thresholds["strong_watch"] >= thresholds["watch"] >= thresholds["neutral"]):
            raise AgentConfigError("AI committee recommendation thresholds must be descending.")

    def summary(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "input_top_n": self.input_top_n,
            "final_top_n": self.final_top_n,
            "persist_default": self.persist_default,
            "batch_size": self.batch_size,
            "mock_only": self.mock_only,
            "enabled_agents": list(self.enabled_agents),
            "weights": {name: float(weight) for name, weight in self.score_weights.items()},
            "thresholds": {
                name: float(value)
                for name, value in self.recommendation_thresholds.items()
            },
            "routes": {
                name: {
                    "enabled": config.enabled,
                    "provider": config.provider,
                    "model": config.model,
                    "weight": float(config.weight),
                }
                for name, config in self.agents.items()
            },
        }


def load_ai_committee_config() -> AICommitteeConfig:
    models = get_app_config().config_files.get("models", {})
    llm_config = models.get("llm", {})
    raw = DEFAULT_AI_COMMITTEE_CONFIG | models.get("ai_committee", {})
    if "agents" in models.get("ai_committee", {}):
        raw["agents"] = DEFAULT_AGENT_CONFIGS | models["ai_committee"]["agents"]
    mock_only = bool(llm_config.get("mock_only", True))
    return AICommitteeConfig(raw=raw, mock_only=mock_only)
