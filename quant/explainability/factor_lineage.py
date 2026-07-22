from __future__ import annotations

from dataclasses import asdict, dataclass

from quant.explainability.factor_registry import FACTOR_REGISTRY


@dataclass(frozen=True)
class FactorLineage:
    raw_metric: str
    subfactor: str
    factor_family: str
    decision_path: tuple[str, ...]

    def as_dict(self) -> dict:
        value = asdict(self)
        value["decision_path"] = list(self.decision_path)
        return value


def lineage_for_metrics(raw_metrics: list[str] | tuple[str, ...]) -> list[FactorLineage]:
    output: list[FactorLineage] = []
    for metric in raw_metrics:
        definition = FACTOR_REGISTRY.get(metric)
        if definition is None:
            continue
        output.append(
            FactorLineage(
                raw_metric=definition.raw_metric,
                subfactor=definition.subfactor,
                factor_family=definition.factor_family,
                decision_path=(
                    definition.raw_metric,
                    definition.subfactor,
                    definition.factor_family,
                    "QUANT",
                    "ENTRY_TIMING",
                    "ADMISSION_V3_SHADOW",
                    "FINAL_EXPLANATION",
                ),
            )
        )
    return output
