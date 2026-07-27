from __future__ import annotations

from datetime import date

import pandas as pd

from market_forecast.config import WalkForwardConfig
from market_forecast.walk_forward import build_walk_forward_folds


def _metadata(end: str = "2020-12-31") -> pd.DataFrame:
    dates = pd.bdate_range("2005-01-04", end)
    return pd.DataFrame({"prediction_for_date": dates})


def test_walk_forward_uses_strict_annual_boundaries() -> None:
    folds = build_walk_forward_folds(_metadata(), WalkForwardConfig(), current_date=date(2021, 1, 1))
    first = folds[0]

    assert first.test_year == 2015
    assert first.train_end.startswith("2013-")
    assert first.validation_start.startswith("2014-")
    assert first.test_start.startswith("2015-")
    assert max(first.train_indexes) < min(first.validation_indexes) < min(first.test_indexes)


def test_truncating_future_years_does_not_change_earlier_fold() -> None:
    config = WalkForwardConfig()
    full = build_walk_forward_folds(_metadata("2020-12-31"), config, current_date=date(2021, 1, 1))
    truncated = build_walk_forward_folds(_metadata("2018-12-31"), config, current_date=date(2019, 1, 1))

    assert full[0].boundary_record() == truncated[0].boundary_record()
