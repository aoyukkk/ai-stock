from __future__ import annotations

from pathlib import Path

import yaml


ROOT_DIR = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT_DIR / "config" / "ranking_evaluation.yaml"

EVALUATION_VERSION = "RANKING_FORWARD_EFFECTIVENESS_V1"
DEFAULT_HORIZONS = (1, 3, 5, 10)
RETURN_BASIS = "RAW_CLOSE"
EVALUATION_SCOPE = "QUANT_PRE_LLM_TOP100"

OUTCOME_STATUSES = {
    "MATURED",
    "NOT_MATURED",
    "BASELINE_CLOSE_MISSING",
    "FUTURE_CLOSE_MISSING",
    "SUSPENDED_ON_DUE_DATE",
    "DELISTED",
    "DATA_CONFLICT",
    "WRONG_TRADE_DATE",
    "DUPLICATE_SOURCE_DATA",
    "CORPORATE_ACTION_REVIEW",
    "PIPELINE_ERROR",
}

IMMUTABLE_CONFLICT = "RANKING_SNAPSHOT_IMMUTABLE_CONFLICT"
FACTOR_VERSION_REQUIRED = "FACTOR_VERSION_REQUIRED"


def load_config(path: Path | None = None) -> dict:
    source = path or CONFIG_PATH
    raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    value = dict(raw.get("ranking_evaluation") or {})
    value.setdefault("enabled", True)
    value.setdefault("shadow_only", True)
    value.setdefault("activation_date", None)
    value.setdefault("top_n", 100)
    value.setdefault("horizons", list(DEFAULT_HORIZONS))
    value.setdefault("return_basis", RETURN_BASIS)
    value.setdefault("evaluation_version", EVALUATION_VERSION)
    value.setdefault("scheduler_enabled", False)
    if value["return_basis"] != RETURN_BASIS:
        raise ValueError("RANKING_EVALUATION_RETURN_BASIS_MUST_BE_RAW_CLOSE")
    if bool(value.get("scheduler_enabled")):
        raise ValueError("RANKING_EVALUATION_SCHEDULER_MUST_REMAIN_DISABLED")
    if not bool(value.get("shadow_only")):
        raise ValueError("RANKING_EVALUATION_MUST_REMAIN_SHADOW_ONLY")
    horizons = tuple(int(item) for item in value["horizons"])
    if horizons != DEFAULT_HORIZONS:
        raise ValueError("RANKING_EVALUATION_HORIZONS_MUST_BE_D1_D3_D5_D10")
    return value


def original_group(rank: int) -> str:
    if 1 <= rank <= 20:
        return "G1"
    if 21 <= rank <= 40:
        return "G2"
    if 41 <= rank <= 60:
        return "G3"
    if 61 <= rank <= 80:
        return "G4"
    if 81 <= rank <= 100:
        return "G5"
    return "OUT_OF_SCOPE"


def model_name_for(factor_version: str, actionable: bool) -> str:
    upper = str(factor_version or "").upper()
    if "TUSHARE_QUANT_V2_CORRECTED_SHADOW" in upper:
        return "TUSHARE_QUANT_V2_CORRECTED_SHADOW"
    return "TUSHARE_BASELINE_V1" if actionable else str(factor_version or "UNKNOWN")
