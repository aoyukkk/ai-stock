from __future__ import annotations

from decimal import Decimal, ROUND_FLOOR

from position_sizing.schemas import (
    AccountState,
    AllocationResult,
    PositionSizingConfig,
    PositionSuggestion,
    SizingCandidate,
    SizingStatus,
)


ZERO = Decimal("0")


def _lot_floor(quantity: Decimal, lot_size: int) -> int:
    if quantity <= 0:
        return 0
    lots = (quantity / Decimal(lot_size)).to_integral_value(rounding=ROUND_FLOOR)
    return int(lots) * lot_size


class PositionSizingEngine:
    """Pure rule engine. It has no LLM, broker, order, or network dependency."""

    def __init__(self, config: PositionSizingConfig | None = None) -> None:
        self.config = config or PositionSizingConfig()

    def evaluate(self, account: AccountState, candidates: list[SizingCandidate]) -> AllocationResult:
        raw = {item.stock_code: self._conviction(item) for item in candidates}
        raw_total = sum(raw.values(), ZERO)
        suggestions = [
            self._evaluate_one(account, item, raw[item.stock_code] / raw_total if raw_total > 0 else ZERO)
            for item in candidates
        ]
        return AllocationResult(
            suggestions=suggestions,
            total_suggested_capital=sum((item.suggested_capital for item in suggestions), ZERO),
            total_maximum_planned_loss=sum((item.maximum_planned_loss for item in suggestions), ZERO),
        )

    def _conviction(self, item: SizingCandidate) -> Decimal:
        score = item.final_score / Decimal("100")
        conviction = (
            score ** self.config.conviction_power
            * item.controller_confidence
            * item.data_quality_factor
            * item.risk_gate_factor
        )
        if item.unverified_fundamental_research:
            conviction *= self.config.unverified_research_discount
        return conviction

    def _evaluate_one(
        self,
        account: AccountState,
        item: SizingCandidate,
        relative_weight: Decimal,
    ) -> PositionSuggestion:
        upstream_warning = self._upstream_warning(item)
        if upstream_warning:
            return self._zero(item, relative_weight, "upstream_validation", upstream_warning)
        assert item.entry_price is not None and item.stop_price is not None
        risk_per_share = item.entry_price - item.stop_price
        if risk_per_share <= 0:
            return self._zero(item, relative_weight, "invalid_stop", "INVALID_STOP_LOSS")
        if relative_weight <= 0:
            return self._zero(item, relative_weight, "zero_conviction", "Conviction or a gate factor is zero.")

        risk_budget = account.equity * self.config.portfolio_open_risk_percent * relative_weight
        deployable_cash = account.available_cash * self.config.deployable_capital_percent
        existing_stock = account.stock_exposure.get(item.stock_code, ZERO)
        industry_used = account.industry_exposure.get(item.industry, ZERO)
        chain_used = account.chain_exposure.get(item.industry_chain, ZERO)

        quantities = {
            "risk": _lot_floor(risk_budget / risk_per_share, item.lot_size),
            "cash": _lot_floor((deployable_cash * relative_weight) / item.entry_price, item.lot_size),
            "single_stock": _lot_floor(
                max(ZERO, account.equity * self.config.max_single_stock_percent - existing_stock) / item.entry_price,
                item.lot_size,
            ),
            "industry": _lot_floor(
                max(ZERO, account.equity * self.config.max_industry_percent - industry_used) / item.entry_price,
                item.lot_size,
            ),
            "chain": _lot_floor(
                max(ZERO, account.equity * self.config.max_chain_percent - chain_used) / item.entry_price,
                item.lot_size,
            ),
            "liquidity": _lot_floor(
                item.average_daily_amount * self.config.max_liquidity_participation / item.entry_price,
                item.lot_size,
            ),
        }
        binding = min(quantities, key=quantities.get)
        quantity = quantities[binding]
        capital = item.entry_price * quantity
        max_loss = risk_per_share * quantity
        return PositionSuggestion(
            stock_code=item.stock_code,
            status=SizingStatus.SUGGESTED if quantity > 0 else SizingStatus.ZERO_ALLOCATION,
            relative_allocation_weight=relative_weight,
            account_position_percent=capital / account.equity,
            suggested_capital=capital,
            suggested_quantity=quantity,
            risk_per_share=risk_per_share,
            maximum_planned_loss=max_loss,
            binding_constraint=binding,
            constraint_quantities=quantities,
            warnings=self._constraint_warnings(binding, quantity, item),
        )

    def _zero(
        self,
        item: SizingCandidate,
        weight: Decimal,
        constraint: str,
        warning: str,
    ) -> PositionSuggestion:
        return PositionSuggestion(
            stock_code=item.stock_code,
            status=SizingStatus.NEEDS_REVIEW if constraint in {"invalid_stop", "upstream_validation"} else SizingStatus.ZERO_ALLOCATION,
            relative_allocation_weight=weight,
            account_position_percent=ZERO,
            suggested_capital=ZERO,
            suggested_quantity=0,
            risk_per_share=(
                max(ZERO, item.entry_price - item.stop_price)
                if item.entry_price is not None and item.stop_price is not None
                else ZERO
            ),
            maximum_planned_loss=ZERO,
            binding_constraint=constraint,
            constraint_quantities={},
            warnings=[warning],
        )

    def _upstream_warning(self, item: SizingCandidate) -> str | None:
        if item.entry_price is None:
            return "MISSING_ENTRY_PRICE"
        if item.stop_price is None:
            return "MISSING_STOP_LOSS"
        if item.blocked or item.risk_level.upper() in {"BLOCK", "HIGH", "HIGH_RISK", "BLACK_SWAN"}:
            return "UPSTREAM_STOP_LOSS_CONFLICT"
        if item.data_conflict:
            return "DATA_CONFLICT"
        if item.stop_price >= item.entry_price:
            return "INVALID_STOP_LOSS"
        raw_stop = item.unrounded_stop_price if item.unrounded_stop_price is not None else item.stop_price
        raw_stop_distance = (item.entry_price - raw_stop) / item.entry_price
        rounded_stop_distance = (item.entry_price - item.stop_price) / item.entry_price
        tolerance = item.tick_size * Decimal(item.stop_validation_tolerance_ticks) / item.entry_price
        if (
            raw_stop_distance > self.config.max_stop_loss_percent
            or rounded_stop_distance > self.config.max_stop_loss_percent + tolerance
        ):
            return "UPSTREAM_STOP_LOSS_CONFLICT"
        if item.max_acceptable_price is not None and item.entry_price > item.max_acceptable_price:
            return "ENTRY_ABOVE_MAX_ACCEPTABLE_PRICE"
        if item.risk_reward is not None and item.risk_reward < self.config.minimum_risk_reward:
            return "RISK_REWARD_BELOW_MINIMUM"
        return None

    @staticmethod
    def _constraint_warnings(binding: str, quantity: int, item: SizingCandidate) -> list[str]:
        warnings = []
        if quantity <= 0:
            warnings.append("INSUFFICIENT_LIQUIDITY" if binding == "liquidity" else "BELOW_ONE_TRADING_LOT")
        if binding == "industry":
            warnings.append("INDUSTRY_CONCENTRATION_LIMIT")
        if binding == "chain":
            warnings.append("CHAIN_CONCENTRATION_LIMIT")
        if item.unverified_fundamental_research:
            warnings.append("UNVERIFIED_FUNDAMENTAL_RESEARCH")
        return warnings
