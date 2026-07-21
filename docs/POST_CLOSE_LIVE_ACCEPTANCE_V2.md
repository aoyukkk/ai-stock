# Post-Close Live Acceptance V2

## Current Gate

At implementation time, 2026-07-15 was still in `MORNING_SESSION`. No 2026-07-15 Tushare final cache existed and neither position scope had an explicit confirmation. The live Fast run therefore remained `BLOCKED_MARKET_NOT_CLOSED` with `POSITION_SNAPSHOT_REQUIRED`; no advice rows, LLM calls, or orders were created.

## Pool Invariant

The Fast pool now resolves the latest compatible completed Pro run only. For 2026-07-15 it uses the 2026-07-14 pipeline and contains 20 Final candidates. Final, Manual, Human Held, AI Held, and Active Order Plan are visible sources. Raw membership, deduplicated membership, duplicates, distribution, source date, pipeline run, and pool hash are audited. Hidden sources or count violations block the run.

## Position Truth

`PositionTruthGate` distinguishes confirmed positions, confirmed empty, missing, stale, invalid, and conflicted facts. Both Human Reference and AI Simulation scopes require explicit current confirmation. CSV/XLSX confirmation writes an immutable position-truth record. Empty positions require the dedicated confirmation action; missing rows are never interpreted as empty.

Legacy advice runs containing only `POSITION_DATA_MISSING` remain immutable but are surfaced as `LEGACY_RESULT_POSITION_GATE_INVALID`.

## Provider Time

The iFinD realtime `time` field is treated as second-precision exchange quote time in Asia/Shanghai. Delay now uses local receipt time after the request completes. If the source is local receipt or unknown, delay is null and freshness is `TIME_SEMANTICS_UNKNOWN`.

The original zero-delay summary was invalidated and recalculated from persisted provider and receipt timestamps. Corrected P95 delays were approximately 4.43, 3.65, and 5.75 seconds for the three rounds.

## Tiered Coverage

Top100 uses one uniform `PARTIAL_SNAPSHOT_ONLY` scope with a +/-2 overlay cap. Action Pool full ranking includes only stocks with at least 30 one-minute bars and uses `FULL_MINUTE_OVERLAY` with a +/-6 cap. The two ranks are never mixed. Coverage below 95% is `NOT_COMPARABLE_COVERAGE_MISMATCH` and produces no Top20 A/B conclusion.

Historical 2026-07-14 cache validation covered 10/100 Top100 stocks. The overlay range was -0.252175 to +2.0, while the official Quant hash and ranking remained unchanged.

## Fast and Final

Fast uses the latest compatible prior pipeline plus same-day Manual, current confirmed positions, and active plans. Final requires the same-day completed pipeline and Tushare watermark. Fast/Final comparison uses the code union and marks unchanged, more/less conservative, new, removed, and manual-review changes.

No live post-close run was executed before 15:00. The trader must first import positions or explicitly confirm empty, then run Fast after close. Final remains blocked until all same-day Tushare and pipeline gates pass.
