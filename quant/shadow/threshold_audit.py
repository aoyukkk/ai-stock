from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class ThresholdDependency:
    dependency_key: str
    module: str
    source_file: str
    source_line: int | None
    threshold_name: str
    threshold_value: float | None
    score_field: str
    selection_mode: str
    rank_selected: bool
    absolute_score_selected: bool
    global_emotion_shift_can_flip: bool
    notes: str


DEPENDENCIES = (
    ThresholdDependency(
        "ENTRY_TIMING_V1_QUANT_GATE",
        "Entry Timing V1 Admission",
        "entry_timing/admission.py",
        None,
        "minimum_quant_score",
        45.0,
        "quant_score",
        "ABSOLUTE_SCORE",
        False,
        True,
        True,
        "Blocks admission when Quant score is below 45.",
    ),
    ThresholdDependency(
        "ENTRY_TIMING_V2_QUANT_GATE",
        "Entry Timing V2.1 Admission",
        "entry_timing/admission_v2.py",
        None,
        "minimum_quant_score",
        45.0,
        "quant_score",
        "ABSOLUTE_SCORE",
        False,
        True,
        True,
        "Configured in entry_timing_v2_1.yaml; threshold is not modified.",
    ),
    ThresholdDependency(
        "FLASH_INPUT_POOL",
        "Flash Input",
        "trader_demo/service.py",
        None,
        "top_n",
        None,
        "quant_rank",
        "RANK_TOP_N_PLUS_MANUAL",
        True,
        False,
        False,
        "Flash pool is chosen by rank/top_n and explicit manual additions.",
    ),
    ThresholdDependency(
        "PRO_CANDIDATE_POOL",
        "Pro Candidate",
        "trader_demo/pro_resume.py",
        None,
        "selected Flash decisions",
        None,
        "flash_selection",
        "UPSTREAM_SELECTION",
        True,
        False,
        False,
        "No direct Quant absolute-score gate found.",
    ),
    ThresholdDependency(
        "POSITION_WEIGHTED_INPUT",
        "Position Sizing",
        "post_close/actions.py",
        None,
        "weighted composite",
        None,
        "base_quant_score",
        "WEIGHTED_INPUT_NOT_GATE",
        False,
        False,
        False,
        "Quant contributes 50% to a composite; no direct qualification threshold.",
    ),
    ThresholdDependency(
        "ORDER_PLAN_ELIGIBILITY",
        "Order Plan",
        "trader_demo/service.py",
        None,
        "final candidate eligibility",
        None,
        "upstream candidate",
        "UPSTREAM_SELECTION",
        True,
        False,
        False,
        "No direct Quant absolute-score gate found.",
    ),
    ThresholdDependency(
        "TOKEN_COST_FINAL_SCORE_GATE",
        "LLM Cost Control",
        "config/token_cost.yaml",
        None,
        "final_score_above_threshold",
        80.0,
        "final_score",
        "ABSOLUTE_FINAL_SCORE_NOT_DIRECT_QUANT",
        False,
        True,
        False,
        "Final composite score, not raw Quant score.",
    ),
    ThresholdDependency(
        "MOCK_FLASH_ADVANCE",
        "Mock Provider",
        "llm_gateway/mock_provider.py",
        None,
        "quant_score",
        75.0,
        "quant_score",
        "NON_PRODUCTION_MOCK_ABSOLUTE_SCORE",
        False,
        True,
        True,
        "Test/mock-only; not a production prompt or candidate gate.",
    ),
    ThresholdDependency(
        "MOCK_PRO_HIGH",
        "Mock Provider",
        "llm_gateway/mock_provider.py",
        None,
        "quant_score",
        85.0,
        "quant_score",
        "NON_PRODUCTION_MOCK_ABSOLUTE_SCORE",
        False,
        True,
        True,
        "Test/mock-only; not a production prompt or candidate gate.",
    ),
)


def _line_number(root: Path, dependency: ThresholdDependency) -> int | None:
    path = root / dependency.source_file
    if not path.exists():
        return None
    needle = dependency.threshold_name
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if needle in line:
            return number
    return None


def _qualified(rows: Sequence[Mapping[str, Any]], threshold: float) -> set[str]:
    return {
        str(row["stock_code"]).split(".", 1)[0].zfill(6)
        for row in rows
        if float(row["total_score"]) >= threshold
    }


def audit_threshold_dependencies(
    root: Path,
    versions: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    base = versions["S0_LEGACY"]
    output: list[dict[str, Any]] = []
    for dependency in DEPENDENCIES:
        row = asdict(dependency)
        row["source_line"] = _line_number(root, dependency)
        if dependency.score_field == "quant_score" and dependency.threshold_value is not None:
            base_qualified = _qualified(base, dependency.threshold_value)
            for version, field in (
                ("S1_AMOUNT_UNIT_FIX", "s1_flip_count"),
                ("S2_REAL_VOLUME_RATIO", "s2_flip_count"),
                ("S2_1_MISSINGNESS_POLICY", "s2_1_flip_count"),
                ("S3_GLOBAL_EMOTION_REAL", "s3_global_flip_count"),
                ("S3_DIFFERENTIATED_EMOTION", "s3_differentiated_flip_count"),
                ("S4_PERCENTILE_NORMALIZATION", "s4_flip_count"),
            ):
                row[field] = len(
                    base_qualified ^ _qualified(versions[version], dependency.threshold_value)
                )
        else:
            for field in (
                "s1_flip_count",
                "s2_flip_count",
                "s2_1_flip_count",
                "s3_global_flip_count",
                "s3_differentiated_flip_count",
                "s4_flip_count",
            ):
                row[field] = None
        output.append(row)
    return output
