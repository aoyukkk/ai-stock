from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd


def synthetic_index_frame(start: str = "2005-01-04", periods: int = 5000) -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=periods)
    index = np.arange(periods, dtype=float)
    returns = 0.0002 + 0.004 * np.sin(index / 11.0) + 0.002 * np.cos(index / 37.0)
    close = 3000.0 * np.exp(np.cumsum(returns))
    pre_close = np.concatenate([[close[0] / (1.0 + returns[0])], close[:-1]])
    open_price = pre_close * (1.0 + 0.0015 * np.sin(index / 7.0))
    high = np.maximum(open_price, close) * 1.004
    low = np.minimum(open_price, close) * 0.996
    volume = 1.0e8 * (1.0 + 0.15 * np.sin(index / 17.0))
    amount = volume * close / 100.0
    return pd.DataFrame(
        {
            "ts_code": "000001.SH",
            "trade_date": dates,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "pre_close": pre_close,
            "change": close - pre_close,
            "pct_chg": (close / pre_close - 1.0) * 100.0,
            "vol": volume,
            "amount": amount,
        }
    )


def synthetic_calendar(frame: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "exchange": "SSE",
            "cal_date": pd.to_datetime(frame["trade_date"]),
            "is_open": 1,
            "pretrade_date": pd.to_datetime(frame["trade_date"]).shift(1),
        }
    )


class FakeTushareProvider:
    def __init__(self, frame: pd.DataFrame, calendar: pd.DataFrame) -> None:
        self.frame = frame.copy()
        self.calendar = calendar.copy()
        self.calls: list[tuple[str, dict]] = []

    def query_endpoint(self, api_name: str, params: dict, **_kwargs):
        self.calls.append((api_name, dict(params)))
        start = pd.Timestamp(str(params["start_date"]))
        end = pd.Timestamp(str(params["end_date"]))
        if api_name == "trade_cal":
            values = self.calendar.copy()
            date_values = pd.to_datetime(values["cal_date"])
            values = values[(date_values >= start) & (date_values <= end)]
        elif api_name == "index_daily":
            values = self.frame.copy()
            date_values = pd.to_datetime(values["trade_date"])
            values = values[(date_values >= start) & (date_values <= end)]
        else:
            raise AssertionError(api_name)
        records = values.assign(
            **{
                column: values[column].dt.strftime("%Y%m%d")
                for column in values.columns
                if pd.api.types.is_datetime64_any_dtype(values[column])
            }
        ).to_dict(orient="records")
        return SimpleNamespace(status="available" if records else "empty", records=records, error_type=None, error_message=None)

    def diagnostics(self):
        return {"source": "fake", "call_count": len(self.calls)}
