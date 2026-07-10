from __future__ import annotations

from dataclasses import asdict, dataclass
from math import ceil
from statistics import median
from typing import Iterable


class PipelineBudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class PipelineBudgetConfig:
    daily_limit: int = 5_000_000
    warning_percent: int = 80
    flash_limit: int = 3_500_000
    pro_limit: int = 800_000
    repair_limit: int = 400_000
    final_reserve: int = 300_000

    def validate(self) -> None:
        if min(asdict(self).values()) < 0:
            raise ValueError("TOKEN_BUDGET_MUST_BE_NON_NEGATIVE")
        if self.warning_percent > 100:
            raise ValueError("TOKEN_WARNING_PERCENT_INVALID")
        if self.flash_limit + self.pro_limit + self.repair_limit + self.final_reserve != self.daily_limit:
            raise ValueError("TOKEN_SUB_BUDGETS_MUST_SUM_TO_DAILY_LIMIT")

    @property
    def warning_threshold(self) -> int:
        return self.daily_limit * self.warning_percent // 100

    @property
    def regular_task_stop(self) -> int:
        return self.daily_limit - self.final_reserve


class PipelineBudgetManager:
    def __init__(self, config: PipelineBudgetConfig | None = None) -> None:
        self.config = config or PipelineBudgetConfig()
        self.config.validate()
        self.usage = {"flash": 0, "pro": 0, "repair": 0, "connectivity": 0}
        self.input_usage = {key: 0 for key in self.usage}
        self.output_usage = {key: 0 for key in self.usage}

    def record(self, stage: str, *, input_tokens: int, output_tokens: int) -> None:
        if stage not in self.usage:
            raise ValueError(f"UNKNOWN_TOKEN_STAGE:{stage}")
        total = max(0, int(input_tokens)) + max(0, int(output_tokens))
        projected_stage = self.usage[stage] + total
        if stage in {"repair", "connectivity"}:
            projected_stage = self.usage["repair"] + self.usage["connectivity"] + total
        stage_limit = {
            "flash": self.config.flash_limit,
            "pro": self.config.pro_limit,
            "repair": self.config.repair_limit,
            "connectivity": self.config.repair_limit,
        }[stage]
        if projected_stage > stage_limit:
            raise PipelineBudgetExceeded(f"{stage.upper()}_TOKEN_BUDGET_EXCEEDED")
        if self.total + total >= self.config.regular_task_stop:
            raise PipelineBudgetExceeded("TOTAL_TOKEN_SAFETY_RESERVE_REACHED")
        self.input_usage[stage] += max(0, int(input_tokens))
        self.output_usage[stage] += max(0, int(output_tokens))
        self.usage[stage] = projected_stage

    def assert_projection(self, *, flash: int, pro: int, repair: int) -> None:
        if flash > self.config.flash_limit:
            raise PipelineBudgetExceeded("PROJECTED_FLASH_TOKEN_BUDGET_EXCEEDED")
        if pro > self.config.pro_limit:
            raise PipelineBudgetExceeded("PROJECTED_PRO_TOKEN_BUDGET_EXCEEDED")
        if repair > self.config.repair_limit:
            raise PipelineBudgetExceeded("PROJECTED_REPAIR_TOKEN_BUDGET_EXCEEDED")
        if flash + pro + repair > self.config.regular_task_stop:
            raise PipelineBudgetExceeded("PROJECTED_TOTAL_TOKEN_SAFETY_LIMIT_EXCEEDED")

    @property
    def total(self) -> int:
        return sum(self.usage.values())

    def snapshot(self) -> dict:
        return {
            "limits": {
                **asdict(self.config),
                "warning_threshold": self.config.warning_threshold,
                "regular_task_stop": self.config.regular_task_stop,
            },
            "usage": dict(self.usage),
            "input_usage": dict(self.input_usage),
            "output_usage": dict(self.output_usage),
            "total": self.total,
            "remaining": self.config.daily_limit - self.total,
            "warning_triggered": self.total >= self.config.warning_threshold,
        }


def percentile(values: Iterable[int], percent: int) -> int:
    clean = sorted(max(0, int(value)) for value in values)
    if not clean:
        return 0
    index = max(0, min(len(clean) - 1, ceil(percent / 100 * len(clean)) - 1))
    return clean[index]


def canary_projection(
    input_tokens: list[int],
    output_tokens: list[int],
    *,
    total_task_count: int,
    repair_probability: float = 0.10,
) -> dict[str, int | float]:
    input_p50 = int(median(input_tokens)) if input_tokens else 0
    output_p50 = int(median(output_tokens)) if output_tokens else 0
    input_p90 = percentile(input_tokens, 90)
    output_p90 = percentile(output_tokens, 90)
    expected = (input_p50 + output_p50) * total_task_count
    reserved = (input_p90 + output_p90) * total_task_count
    projected_repair = int(reserved * max(0.0, repair_probability))
    return {
        "input_p50": input_p50,
        "input_p90": input_p90,
        "output_p50": output_p50,
        "output_p90": output_p90,
        "expected_tokens": expected,
        "reserved_tokens": reserved,
        "projected_repair_tokens": projected_repair,
        "projected_total": reserved + projected_repair,
    }
