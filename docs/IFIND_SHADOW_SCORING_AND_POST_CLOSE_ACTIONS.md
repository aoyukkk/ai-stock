# iFinD Shadow Scoring and Post-Close Actions

## Safety Boundary

- The official scoring profile remains `TUSHARE_BASELINE_V1`.
- `IFIND_SHADOW_V1` is stored and displayed separately and cannot change Quant Top100, Flash, Pro, order plans, or position sizing.
- `IFIND_ENHANCED_V1` is disabled and requires future forward validation plus explicit user approval.
- The post-close module produces advisory snapshots only. It does not create real or virtual orders.
- Fast advice does not call an LLM. Optional Pro review goes through the LLM Gateway and may only confirm, become more conservative, or request manual review.
- Real trading and the scheduler remain disabled.

## Open-Session Acceptance

The accepted run is `ifind-accept-b128ed6846a24dddac48`. It completed three live Shadow rounds on 2026-07-15 with 60-second intervals. Each round returned 8/8 indices and 20/20 selected stocks. The third round also validated ten completed one-minute bars for each of three stocks. No business tables changed and no LLM was called.

The promotion recommendation is `READY_FOR_SELECTED_POOL_MONITOR`. This is a capability result only and does not alter runtime scoring or provider routing.

Reports are retained under `data/reports/ifind/acceptance/`. The earlier failed attempt is intentionally retained for auditability.

## Dual Scoring

The enhancement engine runs only over Base Quant Top100. Its six components are relative strength, close quality, tail strength, intraday stability, liquidity confirmation, and market-regime fit. Overlapping daily OHLC, pre-close, percentage change, volume, and amount remain Tushare-owned and are used only for cross-provider validation.

Missing iFinD data is not scored as zero. The engine records `ifind_eod_score=null`, `overlay_delta=0`, and `enhanced_score=base_score` with a fallback reason. Material conflicts also force a zero overlay. The normal overlay is bounded to +/-6; snapshot-only input is bounded to +/-2.

The historical 2026-07-14 run found no overlap between the existing monitored iFinD pool and the exact Quant Top100. All 100 rows therefore used the audited baseline fallback. Official ranking hashes remained unchanged.

## Position Facts

Human and AI-simulation positions use separate immutable snapshot scopes. CSV/XLSX imports require preview, validation, and explicit confirmation. An empty confirmed human snapshot is the auditable representation of no human holdings.

Until a human snapshot has been confirmed, selected stocks use `POSITION_DATA_MISSING` and receive `MANUAL_REVIEW`. They are not silently treated as non-held candidates.

The next target session is resolved from cached Tushare trade-calendar data. The Fast rule path does not wait on an external calendar request.

## Post-Close Runtime

- `POST_CLOSE_FAST` is allowed only after 15:00 Asia/Shanghai for the requested current trade date, unless a CLI historical replay is explicitly requested.
- `POST_CLOSE_FINAL` additionally requires final Tushare daily data.
- A before-close request returns `WAITING_FOR_POST_CLOSE_RUN` and writes no advice run.
- Fast results are persisted as immutable versions and remain available if optional Pro review fails.
- Held and candidate action enums are separate. Non-held candidates cannot receive sell, reduce, or exit advice.

The 2026-07-14 historical replay completed in 263 ms for 64 stocks. Because no confirmed human-position snapshot existed, all 64 results were `MANUAL_REVIEW`; no orders and no LLM calls were produced.

## Outputs

The daily workbook includes `07_盘后操作建议`. The standalone workbook contains held advice, confirmed non-held candidate advice, baseline-versus-shadow comparison, and rule/exception sheets. All cells are horizontally and vertically centered with wrapped text, and stock codes use text format.

