# Current-Mainline Reconciliation and Quant Calibration Report

## Phase

Current-Mainline Reconciliation + Point-in-Time Adjusted Technical Factors + Stock-Limit Risk Subfactor Integration

## Current-mainline baseline

- Branch/HEAD at reconciliation start: `main` / `3200ed3`.
- Current TemporalConsistencyGate, quant persistence, database migrations, fundamentals, LLM gateway, validation, and trader-demo changes were preserved.
- Five major weights remain unchanged: technical 0.25, capital 0.25, emotion 0.20, momentum 0.15, risk 0.15.

## Old-branch reconciliation

| Feature | Before reconciliation | After reconciliation |
| --- | --- | --- |
| adj_factor coverage | YES | YES |
| adj_factor factor_detail | YES | YES |
| adjusted technical series | NO | YES |
| stk_limit prices | YES | YES |
| limit_status | YES, basic states | YES, complete states |
| near-limit detection | YES | YES |
| consecutive-limit count | YES | YES |
| limit risk note | YES | YES |
| limit subfactor affects risk score | NO | YES, opt-in |
| point-in-time safeguards | YES, general gate | YES, adjusted-factor enforcement |

Only missing adjusted-series and risk-subfactor increments were merged. No older implementation replaced current mainline modules.

## Adj-factor modes

- `RAW`: production default and exact legacy score path.
- `QFQ_POINT_IN_TIME`: Tushare formula `raw price * trade-date factor / fixed anchor factor`.
- `HFQ_POINT_IN_TIME`: Tushare formula `raw price * trade-date factor`.
- The QFQ anchor is fixed to the requested base/end trade date so historical replay does not drift with the current latest factor.
- `factor_trade_date <= base_market_trade_date` and `factor_available_at <= decision_time` are enforced through the TemporalConsistencyGate helper.
- Availability is conservatively represented as 00:00 Asia/Shanghai on the following calendar day. If legality or factor-series completeness cannot be proven, the run falls back to RAW with an explicit warning.

Adjusted OHLC and pre-close feed MA, MACD diagnostics, RSI, ATR, returns, trend structure, historical high/low, volatility, and drawdown calculations. ATR never mixes raw and adjusted OHLC. VWAP remains RAW because amount and volume are not adjusted; its factor detail states this explicitly.

## Stock-limit risk

Supported states:

`NORMAL`, `NEAR_LIMIT_UP`, `AT_LIMIT_UP`, `OPENED_LIMIT_UP`, `CONSECUTIVE_LIMIT_UP`, `NEAR_LIMIT_DOWN`, `AT_LIMIT_DOWN`, `OPENED_LIMIT_DOWN`, `CONSECUTIVE_LIMIT_DOWN`, `LIMIT_DATA_MISSING`, `NOT_APPLICABLE`.

Risk is a health score where higher is safer. Limit-up states receive no momentum reward; repeated limit-up and all limit-down states reduce the risk-health score. Consecutive counts use the ordered Kline trading sessions and batch `stk_limit` cache. Suspended sessions are absent from Kline data and are not treated as natural-calendar gaps.

Opt-in risk internal weights:

| Subfactor | Weight |
| --- | ---: |
| volatility | 0.30 |
| drawdown | 0.25 |
| liquidity | 0.15 |
| financial | 0.15 |
| price_limit | 0.15 |

With `price_limit.enabled=false`, the legacy three-component risk score is reproduced exactly.

## A/B full-A results

Window: 2026-03-01 through 2026-07-09. Universe and inputs were held constant.

| Run | Mode | Scored | Skipped | Failed | Seconds | Per-stock API | No LLM |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| A | RAW, limit risk off | 5,309 | 9 | 0 | 41.347 | 0 | YES |
| B | RAW, limit risk on | 5,309 | 9 | 0 | 40.504 | 0 | YES |
| C | QFQ point-in-time, limit risk on | 5,309 | 9 | 0 | 48.575 | 0 | YES |

All 5,309 C rows used `QFQ_POINT_IN_TIME`; none silently fell back to RAW.

| Comparison | Rank correlation | Median absolute rank change | Max change | Top20 overlap | Top100 overlap | Top500 overlap |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| A vs B | 0.99916119 | 33 | 539 | 85% | 94% | 97% |
| B vs C | 0.98500738 | 57 | 3,885 | 100% | 98% | 97.8% |
| A vs C | 0.98377005 | 52 | 3,901 | 85% | 92% | 96.2% |

B versus A changed only risk scoring: median technical delta 0, median risk delta +17.9388, maximum absolute risk delta 22.5. C versus B had median technical/risk/total deltas of 0, but maximum absolute deltas of 38.5505 / 40.542 / 19.0967. The large tail rank drift is concentrated in stocks whose corporate-action adjustment materially changes raw continuity and requires further golden-sample review before production activation.

Limit-state counts were stable across all runs: NORMAL 5,163; AT_LIMIT_UP 70; OPENED_LIMIT_UP 22; CONSECUTIVE_LIMIT_UP 6; NEAR_LIMIT_UP 3; AT_LIMIT_DOWN 9; OPENED_LIMIT_DOWN 31; CONSECUTIVE_LIMIT_DOWN 4; LIMIT_DATA_MISSING 1.

## Safety and regression

- Tushare trade-date batch cache remains active.
- `per_stock_api_call_count=0` in A, B, and C.
- `no_llm_call_verified=true` in A, B, and C.
- No LLM, committee, frontend, broker, or real-trading path was added.
- `ENABLE_REAL_TRADING=false` and scheduler safety configuration were not changed.

Temporal gate tests, quant repository tests, database and migration tests, fundamental point-in-time tests, LLM gateway tests, trader-demo Excel tests, frontend structure tests, and backend API tests all passed in the full suite.

## Factor detail and duplicate control

The report includes adjustment basis/mode/version, factor date and availability, anchor date, point-in-time status, RAW and adjusted technical scores, active score, score delta, warning, adjusted latest OHLC/pre-close, complete limit-touch fields, consecutive counts, limit score, internal weight, weighted contribution, and risk note. The explanatory layer checks for an existing `price_limit_risk_score` detail before adding a diagnostic fallback, so the active risk contribution is not duplicated.

## Golden regression

- RAW plus disabled limit risk reproduces the legacy technical and risk scoring paths.
- The focused quant/Temporal/cache suite passed: 26 tests.
- Full project suite: 446 passed, 1 non-blocking Starlette/httpx deprecation warning.
- Security configuration check passed.
- Full project compileall passed.

## Files changed for this phase

- `config/quant_factor.yaml`
- `quant/config.py`
- `quant/factors.py`
- `quant/indicators.py`
- `quant/price_adjustment.py`
- `quant/price_limit.py`
- `quant/ranking.py`
- `quant/schemas.py`
- `scripts/run_real_quant_top500.py`
- `scripts/compare_quant_ab_reports.py`
- `temporal/gate.py`
- `tests/test_stk_limit_risk_factor.py`
- `tests/test_point_in_time_adjustment.py`
- `tests/test_price_limit_risk_scoring.py`
- `docs/CURRENT_MAINLINE_ADJUSTED_FACTOR_LIMIT_RISK_REPORT.md`

Other modified and untracked worktree files predated this phase and were preserved.

## Known limitations

- Conservative adj-factor availability is inferred for daily batch data because Tushare rows do not provide a per-row publication timestamp.
- VWAP stays on RAW prices until a reviewed amount/volume adjustment contract exists.
- Consecutive-limit counting uses available Kline trading sessions rather than an explicit exchange calendar plus suspension ledger.
- QFQ tail drift is large for a small set of stocks and needs named corporate-action golden cases.

## Problems

No blocking implementation or regression problem remains. The principal calibration concern is the large maximum tail rank movement in C despite strong overall rank correlation; this is why adjusted scoring is not enabled by default.

## Recommended production defaults

Keep `technical_factor.price_adjustment.mode=RAW`, `technical_factor.price_adjustment.enabled=false`, and `risk_factor.price_limit.enabled=false`. Use B and C only through explicit CLI/config overrides until the observed ranking drift is accepted.

## Artifacts

- `data/reports/quant_ab_full_a_raw_no_limit.json`
- `data/reports/quant_ab_full_b_raw_limit.json`
- `data/reports/quant_ab_full_c_qfq_limit.json`
- `data/reports/quant_ab_full_comparison.json`

## Suggested git commit message

`feat(quant): add point-in-time price adjustment and limit-risk calibration`

## Next step

Review the largest QFQ rank movers against named ex-rights/ex-dividend events and exchange-calendar suspension data, then decide separately whether B or C should become a production candidate. No production default was changed in this phase.
