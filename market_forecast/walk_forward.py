from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

import numpy as np
import pandas as pd

from market_forecast.config import WalkForwardConfig


class WalkForwardValidationError(RuntimeError):
    pass


@dataclass(frozen=True)
class WalkForwardFold:
    fold_id: str
    test_year: int
    is_partial: bool
    train_indexes: np.ndarray
    validation_indexes: np.ndarray
    test_indexes: np.ndarray
    train_start: str
    train_end: str
    validation_start: str
    validation_end: str
    test_start: str
    test_end: str

    def boundary_record(self) -> dict[str, object]:
        record = asdict(self)
        record.pop("train_indexes")
        record.pop("validation_indexes")
        record.pop("test_indexes")
        record.update(
            {
                "train_sample_count": int(len(self.train_indexes)),
                "validation_sample_count": int(len(self.validation_indexes)),
                "test_sample_count": int(len(self.test_indexes)),
            }
        )
        return record


def build_walk_forward_folds(
    metadata: pd.DataFrame,
    config: WalkForwardConfig,
    *,
    current_date: date | None = None,
) -> list[WalkForwardFold]:
    if metadata.empty:
        raise WalkForwardValidationError("SEQUENCE_METADATA_EMPTY")
    dates = pd.to_datetime(metadata["prediction_for_date"], errors="raise").dt.normalize()
    latest = dates.max()
    earliest = dates.min()
    current = current_date or date.today()
    latest_year = int(latest.year)
    folds: list[WalkForwardFold] = []
    for test_year in range(config.first_test_year, latest_year + 1):
        train_mask = dates.dt.year <= test_year - 2
        validation_mask = dates.dt.year == test_year - 1
        test_mask = dates.dt.year == test_year
        train_indexes = np.flatnonzero(train_mask.to_numpy())
        validation_indexes = np.flatnonzero(validation_mask.to_numpy())
        test_indexes = np.flatnonzero(test_mask.to_numpy())
        if not len(test_indexes):
            continue
        is_partial = test_year == current.year or (test_year == latest_year and latest.month < 12)
        minimum_test = config.minimum_partial_test_samples if is_partial else config.minimum_full_test_samples
        initial_year_span = (pd.Timestamp(dates.iloc[train_indexes[-1]]) - earliest).days / 365.2425 if len(train_indexes) else 0.0
        failures: list[str] = []
        if initial_year_span < config.minimum_initial_train_years:
            failures.append(f"TRAIN_SPAN_LT_{config.minimum_initial_train_years}_YEARS")
        if len(train_indexes) < config.minimum_train_samples:
            failures.append("TRAIN_SAMPLE_COUNT_INSUFFICIENT")
        if len(validation_indexes) < config.minimum_validation_samples:
            failures.append("VALIDATION_SAMPLE_COUNT_INSUFFICIENT")
        if len(test_indexes) < minimum_test:
            failures.append("TEST_SAMPLE_COUNT_INSUFFICIENT")
        if failures:
            raise WalkForwardValidationError(f"{test_year}:{','.join(failures)}")
        train_dates = dates.iloc[train_indexes]
        validation_dates = dates.iloc[validation_indexes]
        test_dates = dates.iloc[test_indexes]
        if not (train_dates.max() < validation_dates.min() <= validation_dates.max() < test_dates.min()):
            raise WalkForwardValidationError(f"{test_year}:TEMPORAL_BOUNDARY_OVERLAP")
        folds.append(
            WalkForwardFold(
                fold_id=f"WF_{test_year}{'_PARTIAL' if is_partial else ''}",
                test_year=test_year,
                is_partial=is_partial,
                train_indexes=train_indexes,
                validation_indexes=validation_indexes,
                test_indexes=test_indexes,
                train_start=train_dates.min().date().isoformat(),
                train_end=train_dates.max().date().isoformat(),
                validation_start=validation_dates.min().date().isoformat(),
                validation_end=validation_dates.max().date().isoformat(),
                test_start=test_dates.min().date().isoformat(),
                test_end=test_dates.max().date().isoformat(),
            )
        )
    if not folds:
        raise WalkForwardValidationError("NO_VALID_WALK_FORWARD_FOLDS")
    if folds[0].test_year > config.first_test_year:
        raise WalkForwardValidationError("FORMAL_OOS_DOES_NOT_START_IN_2015")
    return folds
