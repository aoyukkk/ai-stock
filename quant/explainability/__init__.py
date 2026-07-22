"""Shadow-only decision explainability and Admission V3 foundations."""

from quant.explainability.admission_v3 import (
    AdmissionV3Decision,
    AdmissionV3Engine,
    AdmissionV3Input,
    AdmissionV3ShadowEV,
    calculate_shadow_ev,
)
from quant.explainability.factor_attribution import FactorAttributionEngine, FactorAttributionRecord
from quant.explainability.strategy_probability import StrategyProbabilityClassifier, StrategyProbabilityResult
from quant.explainability.timing_contract import (
    INVALID_TIMING_CONTRACT,
    StrategyTimingContractSpec,
    validate_timing_contract,
)

__all__ = [
    "AdmissionV3Decision",
    "AdmissionV3Engine",
    "AdmissionV3Input",
    "AdmissionV3ShadowEV",
    "FactorAttributionEngine",
    "FactorAttributionRecord",
    "INVALID_TIMING_CONTRACT",
    "StrategyProbabilityClassifier",
    "StrategyProbabilityResult",
    "StrategyTimingContractSpec",
    "validate_timing_contract",
    "calculate_shadow_ev",
]
