from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from datasource.schemas import KlineBar
from temporal.gate import TemporalConsistencyGate


RAW = "RAW"
QFQ_POINT_IN_TIME = "QFQ_POINT_IN_TIME"
HFQ_POINT_IN_TIME = "HFQ_POINT_IN_TIME"
ADJUSTMENT_MODES = {RAW, QFQ_POINT_IN_TIME, HFQ_POINT_IN_TIME}
SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class AdjustedPriceSeries:
    bars: list[KlineBar]
    mode: str
    technical_price_basis: str
    adjusted_series_version: str
    factor_as_of_trade_date: date | None
    factor_available_at: datetime | None
    adjustment_anchor_date: date | None
    point_in_time_status: str
    warning: str | None = None

    @property
    def active(self) -> bool:
        return self.technical_price_basis != RAW


def build_adjusted_price_series(
    bars: list[KlineBar],
    factor_records: dict[str, dict],
    *,
    mode: str,
    decision_time: datetime,
    base_market_trade_date: date | None = None,
    point_in_time_required: bool = True,
    fallback_to_raw: bool = True,
) -> AdjustedPriceSeries:
    normalized_mode = str(mode or RAW).upper()
    if normalized_mode not in ADJUSTMENT_MODES:
        raise ValueError(f"Unsupported adjustment mode: {mode}")
    if normalized_mode == RAW or not bars:
        return _raw_result(bars, normalized_mode, "NOT_REQUESTED")

    base_date = base_market_trade_date or bars[-1].trade_date
    factors = _eligible_factors(factor_records, base_date)
    anchor_date = max(factors, default=None)
    factor_available_at = _conservative_factor_available_at(anchor_date)
    legal = anchor_date is not None and TemporalConsistencyGate.point_in_time_factor_legal(
        factor_trade_date=anchor_date,
        factor_available_at=factor_available_at,
        base_market_trade_date=base_date,
        decision_time=decision_time,
    )
    if point_in_time_required and not legal:
        return _fallback_or_raise(
            bars,
            normalized_mode,
            fallback_to_raw,
            factor_as_of_trade_date=anchor_date,
            factor_available_at=factor_available_at,
            anchor_date=anchor_date,
            warning="POINT_IN_TIME_ADJUSTMENT_UNAVAILABLE",
        )

    missing_dates = [bar.trade_date for bar in bars if bar.trade_date not in factors]
    if anchor_date is None or missing_dates:
        return _fallback_or_raise(
            bars,
            normalized_mode,
            fallback_to_raw,
            factor_as_of_trade_date=anchor_date,
            factor_available_at=factor_available_at,
            anchor_date=anchor_date,
            warning="ADJUSTMENT_FACTOR_SERIES_INCOMPLETE",
        )

    anchor_factor = factors[anchor_date]
    adjusted: list[KlineBar] = []
    for bar in bars:
        factor = factors[bar.trade_date]
        multiplier = factor / anchor_factor if normalized_mode == QFQ_POINT_IN_TIME else factor
        adjusted.append(
            bar.model_copy(
                update={
                    "open": _adjust(bar.open, multiplier),
                    "high": _adjust(bar.high, multiplier),
                    "low": _adjust(bar.low, multiplier),
                    "close": _adjust(bar.close, multiplier),
                    "pre_close": _adjust(bar.pre_close, multiplier),
                }
            )
        )
    return AdjustedPriceSeries(
        bars=adjusted,
        mode=normalized_mode,
        technical_price_basis=normalized_mode,
        adjusted_series_version=f"tushare-adj-factor-v1:{normalized_mode}:{anchor_date.isoformat()}",
        factor_as_of_trade_date=anchor_date,
        factor_available_at=factor_available_at,
        adjustment_anchor_date=anchor_date,
        point_in_time_status="PASS" if legal else "NOT_REQUIRED",
    )


def _eligible_factors(records: dict[str, dict], base_date: date) -> dict[date, Decimal]:
    factors: dict[date, Decimal] = {}
    for raw_date, row in records.items():
        factor_date = _parse_date(raw_date)
        if factor_date is None or factor_date > base_date:
            continue
        value = row.get("adj_factor")
        if value in (None, ""):
            continue
        factor = Decimal(str(value))
        if factor > 0:
            factors[factor_date] = factor
    return factors


def _conservative_factor_available_at(factor_date: date | None) -> datetime | None:
    if factor_date is None:
        return None
    return datetime.combine(factor_date + timedelta(days=1), time.min, tzinfo=SHANGHAI)


def _fallback_or_raise(
    bars: list[KlineBar],
    mode: str,
    fallback_to_raw: bool,
    *,
    factor_as_of_trade_date: date | None,
    factor_available_at: datetime | None,
    anchor_date: date | None,
    warning: str,
) -> AdjustedPriceSeries:
    if not fallback_to_raw:
        raise ValueError(warning)
    return AdjustedPriceSeries(
        bars=list(bars),
        mode=mode,
        technical_price_basis=RAW,
        adjusted_series_version="raw-v1",
        factor_as_of_trade_date=factor_as_of_trade_date,
        factor_available_at=factor_available_at,
        adjustment_anchor_date=anchor_date,
        point_in_time_status="FALLBACK_RAW",
        warning=warning,
    )


def _raw_result(bars: list[KlineBar], mode: str, status: str) -> AdjustedPriceSeries:
    return AdjustedPriceSeries(
        bars=list(bars),
        mode=mode,
        technical_price_basis=RAW,
        adjusted_series_version="raw-v1",
        factor_as_of_trade_date=None,
        factor_available_at=None,
        adjustment_anchor_date=None,
        point_in_time_status=status,
    )


def _parse_date(value: str) -> date | None:
    normalized = str(value or "").split("T", 1)[0].replace("-", "")
    if len(normalized) != 8 or not normalized.isdigit():
        return None
    return datetime.strptime(normalized, "%Y%m%d").date()


def _adjust(value, multiplier: Decimal) -> Decimal:
    return (Decimal(str(value)) * multiplier).quantize(Decimal("0.0001"))
