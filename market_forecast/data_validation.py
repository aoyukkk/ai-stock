from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


INDEX_FIELDS = (
    "ts_code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "change",
    "pct_chg",
    "vol",
    "amount",
)


class MarketDataValidationError(RuntimeError):
    pass


@dataclass(frozen=True)
class DataQualityResult:
    frame: pd.DataFrame
    calendar: pd.DataFrame
    report: dict[str, Any]


def validate_market_data(
    frame: pd.DataFrame,
    calendar: pd.DataFrame,
    *,
    index_code: str,
    expected_latest_trade_date: pd.Timestamp,
) -> DataQualityResult:
    missing_fields = sorted(set(INDEX_FIELDS) - set(frame.columns))
    calendar_missing = sorted({"cal_date", "is_open"} - set(calendar.columns))
    failures: list[str] = []
    if missing_fields:
        failures.append(f"INDEX_FIELDS_MISSING:{','.join(missing_fields)}")
    if calendar_missing:
        failures.append(f"CALENDAR_FIELDS_MISSING:{','.join(calendar_missing)}")
    if failures:
        raise MarketDataValidationError(";".join(failures))

    clean = frame.loc[:, INDEX_FIELDS].copy()
    clean["trade_date"] = pd.to_datetime(clean["trade_date"], format="mixed", errors="coerce").dt.normalize()
    cal = calendar.loc[:, ["cal_date", "is_open"]].copy()
    cal["cal_date"] = pd.to_datetime(cal["cal_date"], format="mixed", errors="coerce").dt.normalize()
    cal["is_open"] = pd.to_numeric(cal["is_open"], errors="coerce")

    invalid_date_rows = int(clean["trade_date"].isna().sum())
    invalid_calendar_rows = int(cal["cal_date"].isna().sum())
    if invalid_date_rows:
        failures.append(f"INVALID_TRADE_DATE_ROWS:{invalid_date_rows}")
    if invalid_calendar_rows:
        failures.append(f"INVALID_CALENDAR_DATE_ROWS:{invalid_calendar_rows}")

    duplicate_dates = int(clean["trade_date"].duplicated(keep=False).sum())
    duplicate_calendar = int(cal["cal_date"].duplicated(keep=False).sum())
    if duplicate_dates:
        failures.append(f"DUPLICATE_TRADE_DATES:{duplicate_dates}")
    if duplicate_calendar:
        failures.append(f"DUPLICATE_CALENDAR_DATES:{duplicate_calendar}")

    clean = clean.sort_values("trade_date", kind="stable").reset_index(drop=True)
    cal = cal.sort_values("cal_date", kind="stable").reset_index(drop=True)
    if not clean["trade_date"].is_monotonic_increasing:
        failures.append("TRADE_DATE_NOT_INCREASING")
    if not cal["cal_date"].is_monotonic_increasing:
        failures.append("CALENDAR_DATE_NOT_INCREASING")

    numeric_columns = [field for field in INDEX_FIELDS if field not in {"ts_code", "trade_date"}]
    for column in numeric_columns:
        clean[column] = pd.to_numeric(clean[column], errors="coerce")
    missing_counts = {column: int(clean[column].isna().sum()) for column in numeric_columns}
    missing_price_rows = int(clean[["open", "high", "low", "close", "pre_close"]].isna().any(axis=1).sum())
    missing_volume_rows = int(clean[["vol", "amount"]].isna().any(axis=1).sum())
    if missing_price_rows:
        failures.append(f"MISSING_PRICE_ROWS:{missing_price_rows}")
    if missing_volume_rows:
        failures.append(f"MISSING_VOLUME_AMOUNT_ROWS:{missing_volume_rows}")

    nonpositive_prices = int((clean[["open", "high", "low", "close", "pre_close"]] <= 0).any(axis=1).sum())
    bad_high = int((clean["high"] < clean[["open", "close", "low"]].max(axis=1)).sum())
    bad_low = int((clean["low"] > clean[["open", "close", "high"]].min(axis=1)).sum())
    negative_flow = int((clean[["vol", "amount"]] < 0).any(axis=1).sum())
    nonfinite_rows = int((~np.isfinite(clean[numeric_columns].to_numpy(dtype=float))).any(axis=1).sum())
    if nonpositive_prices:
        failures.append(f"NONPOSITIVE_PRICE_ROWS:{nonpositive_prices}")
    if bad_high:
        failures.append(f"INVALID_HIGH_ROWS:{bad_high}")
    if bad_low:
        failures.append(f"INVALID_LOW_ROWS:{bad_low}")
    if negative_flow:
        failures.append(f"NEGATIVE_VOLUME_AMOUNT_ROWS:{negative_flow}")
    if nonfinite_rows:
        failures.append(f"NONFINITE_NUMERIC_ROWS:{nonfinite_rows}")

    unexpected_codes = sorted(set(clean["ts_code"].astype(str)) - {index_code})
    if unexpected_codes:
        failures.append(f"UNEXPECTED_INDEX_CODES:{','.join(unexpected_codes[:5])}")

    open_calendar = cal.loc[cal["is_open"] == 1, "cal_date"]
    if clean.empty:
        failures.append("INDEX_DAILY_EMPTY")
        actual_latest = None
        missing_open_dates: list[str] = []
        extra_index_dates: list[str] = []
    else:
        actual_latest = clean["trade_date"].iloc[-1]
        applicable_calendar = open_calendar[
            (open_calendar >= clean["trade_date"].iloc[0]) & (open_calendar <= expected_latest_trade_date)
        ]
        index_dates = set(clean.loc[clean["trade_date"] <= expected_latest_trade_date, "trade_date"])
        calendar_dates = set(applicable_calendar)
        missing_open_dates = [value.date().isoformat() for value in sorted(calendar_dates - index_dates)]
        extra_index_dates = [value.date().isoformat() for value in sorted(index_dates - calendar_dates)]
        if actual_latest != expected_latest_trade_date:
            failures.append(
                "LATEST_TRADE_DATE_MISMATCH:"
                f"expected={expected_latest_trade_date.date().isoformat()},actual={actual_latest.date().isoformat()}"
            )
        if missing_open_dates:
            failures.append(f"MISSING_OPEN_TRADE_DATES:{len(missing_open_dates)}")
        if extra_index_dates:
            failures.append(f"INDEX_DATES_NOT_IN_CALENDAR:{len(extra_index_dates)}")

    report = {
        "status": "PASS" if not failures else "FAIL",
        "index_code": index_code,
        "row_count": int(len(clean)),
        "date_start": clean["trade_date"].iloc[0].date().isoformat() if not clean.empty else None,
        "date_end": actual_latest.date().isoformat() if actual_latest is not None else None,
        "expected_latest_trade_date": expected_latest_trade_date.date().isoformat(),
        "duplicate_trade_date_rows": duplicate_dates,
        "duplicate_calendar_rows": duplicate_calendar,
        "missing_value_counts": missing_counts,
        "missing_price_rows": missing_price_rows,
        "missing_volume_amount_rows": missing_volume_rows,
        "nonpositive_price_rows": nonpositive_prices,
        "invalid_high_rows": bad_high,
        "invalid_low_rows": bad_low,
        "negative_volume_amount_rows": negative_flow,
        "nonfinite_numeric_rows": nonfinite_rows,
        "missing_open_trade_dates": missing_open_dates,
        "extra_index_dates": extra_index_dates,
        "failures": failures,
        "price_forward_fill_used": False,
    }
    if failures:
        raise MarketDataValidationError(";".join(failures))
    return DataQualityResult(clean, cal, report)
