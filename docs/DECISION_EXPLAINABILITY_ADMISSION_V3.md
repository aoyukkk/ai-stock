# Decision Explainability + Admission V3 Foundation

This phase is an isolated, append-only shadow system. It reads existing Quant and Entry Timing V2.2 inputs, but it does not change production ranking, prompts, order creation, position state, or trading configuration.

## Fixed baseline

- Scoring profile: `TUSHARE_BASELINE_V1`
- Quant weights: `25 / 25 / 20 / 15 / 15`
- Entry Timing source: `V2.2 Shadow`
- Existing Admission source: `V2.2 Shadow`
- Admission V3: shadow only

## Timing contract

Every `admission_v3_result` has a non-null foreign key to `strategy_timing_contract`.

```text
observation_end_ts <= available_at_ts <= signal_generated_at < order_eligible_at
```

Invalid ordering raises `INVALID_TIMING_CONTRACT`. The shared forward-return guard validates this contract before it invokes any return calculator.

## Explainability chain

The factor registry and lineage preserve this path:

```text
raw metric -> subfactor -> factor family -> Quant -> Entry Timing -> Admission V3 Shadow -> final explanation
```

Supported families are `MOMENTUM`, `VOLUME_CAPITAL`, `POSITION_TREND`, `SENTIMENT_REGIME`, `FUNDAMENTAL`, and `RISK_LIQUIDITY`. The attribution layer projects the frozen Quant weights; it does not reweight Quant. Fundamental and structured LLM fields are explanatory Shadow context with zero production Quant weight.

## Admission V3 layers

1. Hard safety gates: future data, suspension, ST, untradeable, and black swan conditions are `REJECT`.
2. Risk penalties: market RED, high-position risk, and liquidity risk reduce score and position size; they are not direct blocks.
3. Opportunity score: strategy probability, Entry Timing, sector strength, and momentum.
4. Portfolio adjustments: industry concentration, correlation, and capacity.

`OPEN_SET` is routed to `REVIEW`, not directly rejected. Each applied soft gate stores the state, score, and position result that would occur if that gate were removed.

## Structured LLM shadow contract

The LLM-facing shadow schema contains only `sector_catalyst_score`, `business_quality_score`, `risk_score`, `holding_period_fit`, and `evidence_confidence`. A deterministic local function calculates `final_llm_score`. The LLM never assigns final rank, and this phase makes zero LLM calls.

## Run and inspect

```powershell
conda run -n ai-stock-agent python scripts/run_decision_explainability_shadow.py --trade-date 2026-07-20 --confirm-shadow
```

The frontend page `/decision-explainability` and all `/api/workbench/decision-explainability/*` endpoints are read-only. Gate return value fields remain pending until legally available forward returns mature.

## Factor performance and gate value enhancement

`factor_performance_history` joins the latest successful Admission V3 Shadow snapshot for each selection date to the latest successful persisted selection-performance return snapshot. Repeated performance runs are deduplicated by selection date, stock, and holding day. Only non-zero factor contributions with a valid Timing Contract and a matured return enter the sample.

The reported contribution sign is realized-direction agreement:

- positive contribution: factor rank contribution and realized terminal return have the same sign;
- negative contribution: their signs differ;
- zero contributions do not enter the factor-family sample.

D1, D3, and D5 remain separate. Win rate uses the longest legally available horizon up to D5 for each stock. This is descriptive validation and never changes a Quant weight.

`GateEvaluationService` normalizes the Shadow gates to `High Position Risk`, `Market Emotion Gate`, `Liquidity Gate`, `Strategy Gate`, and `Data Gate`. Avoided loss and missed gain use only matured returns for candidates whose admission state would improve when the gate is removed. False-positive rate is the fraction of those blocked candidates that later produced a positive return.

The added `expected_value_score` and `risk_adjusted_opportunity_score` are Shadow diagnostics. They are persisted beside the original V3 score and never replace it. Strategy probability now exposes exactly `TREND_BREAKOUT`, `STRONG_PULLBACK`, `SECTOR_RESONANCE`, `OVERSOLD_REBOUND`, and `OPEN_SET`, summing to one.

Run the additive migration and historical Shadow evaluation with:

```powershell
conda run -n ai-stock-agent python scripts/migrate_admission_v3_explainability_enhancement.py
conda run -n ai-stock-agent python scripts/run_explainability_evaluation_shadow.py --start-date 2026-07-13 --end-date 2026-07-20 --confirm-shadow
```
