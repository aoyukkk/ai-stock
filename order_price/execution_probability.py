from __future__ import annotations

from decimal import Decimal


def estimate_fill_probability(
    candidate_price: Decimal,
    latest_price: Decimal,
    support: Decimal,
    resistance: Decimal,
    volume: int,
    amount: Decimal,
    side: str = "BUY",
) -> Decimal:
    if latest_price <= 0 or resistance <= support:
        return Decimal("0.5000")

    if side.upper() != "BUY":
        return Decimal("0.5000")

    if candidate_price >= latest_price:
        base = Decimal("0.8500")
    else:
        price_band = max(resistance - support, Decimal("0.01"))
        support_distance = abs(candidate_price - support) / price_band
        latest_distance = max(latest_price - candidate_price, Decimal("0")) / max(latest_price, Decimal("0.01"))
        base = Decimal("0.6500") - support_distance * Decimal("0.2000") - latest_distance * Decimal("1.5000")

    liquidity_boost = min(Decimal(str(amount)) / Decimal("1000000000"), Decimal("0.0800"))
    volume_boost = min(Decimal(volume) / Decimal("100000000"), Decimal("0.0500"))
    return _clamp_probability(base + liquidity_boost + volume_boost)


def _clamp_probability(value: Decimal) -> Decimal:
    return max(Decimal("0"), min(Decimal("1"), value)).quantize(Decimal("0.0001"))
