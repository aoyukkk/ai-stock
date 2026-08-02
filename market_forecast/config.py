from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml


ALLOWED_DEVICES = {"auto", "cpu", "cuda"}
FIXED_SEEDS = (7, 42, 2026)


@dataclass(frozen=True)
class DataConfig:
    index_code: str = "000001.SH"
    exchange: str = "SSE"
    start_date: str = "20050101"
    end_date: str = "auto"
    cache_root: str = "data/cache/market_forecast"
    market_close_time: str = "15:30"


@dataclass(frozen=True)
class FeatureConfig:
    epsilon: float = 1e-12


@dataclass(frozen=True)
class SequenceConfig:
    lookback_days: int = 60


@dataclass(frozen=True)
class WalkForwardConfig:
    first_test_year: int = 2015
    minimum_initial_train_years: int = 8
    minimum_train_samples: int = 1500
    minimum_validation_samples: int = 150
    minimum_full_test_samples: int = 150
    minimum_partial_test_samples: int = 50


@dataclass(frozen=True)
class ModelConfig:
    hidden_size: int = 32
    num_layers: int = 2
    dropout: float = 0.20
    output_size: int = 1
    learning_rate: float = 0.001
    weight_decay: float = 0.0001
    batch_size: int = 64
    max_epochs: int = 100
    early_stopping_patience: int = 10
    gradient_clip_norm: float = 1.0
    probability_threshold: float = 0.50


@dataclass(frozen=True)
class BootstrapConfig:
    block_length: int = 20
    resamples: int = 5000
    random_seed: int = 20260721


@dataclass(frozen=True)
class DecisionConfig:
    minimum_accuracy_improvement: float = 0.01
    significance_level: float = 0.05
    minimum_folds_beating_baseline_ratio: float = 0.60
    maximum_seed_accuracy_std: float = 0.01
    near_random_lower: float = 0.49
    near_random_upper: float = 0.51


@dataclass(frozen=True)
class ExperimentConfig:
    experiment_name: str = "Market Direction GRU Feasibility Experiment"
    data: DataConfig = field(default_factory=DataConfig)
    features: FeatureConfig = field(default_factory=FeatureConfig)
    sequence: SequenceConfig = field(default_factory=SequenceConfig)
    walk_forward: WalkForwardConfig = field(default_factory=WalkForwardConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    bootstrap: BootstrapConfig = field(default_factory=BootstrapConfig)
    decision: DecisionConfig = field(default_factory=DecisionConfig)
    seeds: tuple[int, ...] = FIXED_SEEDS
    device: str = "auto"
    quick_test: bool = False

    def validate(self) -> None:
        if self.data.index_code != "000001.SH":
            raise ValueError("This frozen phase supports only 000001.SH")
        if self.data.exchange != "SSE":
            raise ValueError("This frozen phase requires the SSE trade calendar")
        if self.sequence.lookback_days < 2:
            raise ValueError("lookback_days must be at least 2")
        if not self.quick_test and tuple(self.seeds) != FIXED_SEEDS:
            raise ValueError(f"Formal runs require seeds {FIXED_SEEDS}")
        if self.device not in ALLOWED_DEVICES:
            raise ValueError(f"device must be one of {sorted(ALLOWED_DEVICES)}")
        if self.model.probability_threshold != 0.50:
            raise ValueError("The frozen probability threshold is 0.50")
        if self.bootstrap.resamples < 5000 and not self.quick_test:
            raise ValueError("Formal runs require at least 5000 bootstrap resamples")
        if self.walk_forward.minimum_initial_train_years < 8:
            raise ValueError("Initial training window must cover at least eight years")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_experiment_config(path: str | Path) -> ExperimentConfig:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    config = ExperimentConfig(
        experiment_name=str(payload.get("experiment_name") or ExperimentConfig.experiment_name),
        data=DataConfig(**payload.get("data", {})),
        features=FeatureConfig(**payload.get("features", {})),
        sequence=SequenceConfig(**payload.get("sequence", {})),
        walk_forward=WalkForwardConfig(**payload.get("walk_forward", {})),
        model=ModelConfig(**payload.get("model", {})),
        bootstrap=BootstrapConfig(**payload.get("bootstrap", {})),
        decision=DecisionConfig(**payload.get("decision", {})),
        seeds=tuple(int(value) for value in payload.get("seeds", FIXED_SEEDS)),
        device=str(payload.get("device", "auto")),
        quick_test=bool(payload.get("quick_test", False)),
    )
    config.validate()
    return config
