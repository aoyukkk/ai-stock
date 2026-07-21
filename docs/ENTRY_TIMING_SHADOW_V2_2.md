# Short-Term Trading Admission V2.2 Shadow

## Scope

V2.2 adds market-adjusted evaluation, persistent market regimes, regime-aware deployment,
portfolio concentration control, and an intraday entry trigger. Every component is shadow-only.
It does not change Quant, Flash V4, Pro, Entry Timing V1/V2.1, or Admission V1/V2.1.

## Decision Order

1. Admission V2.1 decides whether a stock is an eligible candidate.
2. Market Regime V2 classifies the point-in-time market as `RISK_ON`, `ROTATION`, `REPAIR`,
   `RISK_OFF`, or `CRASH` with crash cooldown and risk-on confirmation.
3. Regime Deployment Gate applies the configured candidate limit and advisory position multiplier.
4. Portfolio Concentration Gate ranks by admission score, sector strength, and existing rank, then
   applies industry and correlation-cluster limits. Model and manual pools are evaluated separately.
5. Intraday Entry Trigger evaluates only retained candidates. Missing minute bars produce
   `DATA_INSUFFICIENT`; daily bars are never used to fabricate intraday triggers.

## Evaluation

Stock D1/D3/D5 returns are displayed together with benchmark D1/D3/D5 returns and alpha. The
benchmark preference is a locally available point-in-time industry series, then the all-A
equal-weight return. Missing benchmarks stay missing and are never filled with zero.

The management dashboard keeps five independent views: absolute win rate, market-relative win
rate, target-before-stop rate, risk avoidance, and no-trade quality/opportunity cost. Historical
target-before-stop remains empty when valid intraday target/stop ordering is unavailable.

## Manual Run

```powershell
python scripts/run_entry_timing_v22_shadow.py --historical-start 2026-07-13 --historical-end 2026-07-17
```

The replay reads only the configured local database and Tushare cache. It writes an eight-sheet
workbook under `outputs/<end-date>/复盘/`. Repeating the same run is idempotent and must retain the
same reproducible hash.

## Safety

- `enabled: false` and `shadow_only: true` by default.
- Scheduler, LLM calls, external historical calls, parameter search, real orders, and virtual
  orders are disabled.
- Promotion is never automatic. Fewer than 20 trading days or 100 complete D3 samples must remain
  `KEEP_V2_2_SHADOW`.
