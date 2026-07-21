# API Endpoints Overview

## Selected-Stock Intraday Monitor V1

The `/api/workbench/intraday-monitor` contract is independent from midday run IDs,
checkpoints, result caches, and business tables. Monitoring is disabled by default,
SHADOW-only, observation-only, and must be started explicitly by the trader.

- Session: create, start, pause, resume, stop, current, and history endpoints.
- Pool: preview and explicit confirmation, item update/removal, and current pool.
- Midday merge: suggestions, preview, and confirmation; no automatic replacement.
- Rules/results: rule CRUD, selected-stock results, stock detail, and minute bars.
- Alerts: list, unread counts, acknowledge, mute, resolve, dismiss, and on-demand AI explanation.
- Recovery/audit: SSE recovery event and provider usage audit.

Normal monitor requests are P2-P4. The midday path reserves P0 through
`MarketDataRequestBroker`, pauses monitor requests, and preserves pool and alerts.
No monitor endpoint creates, modifies, cancels, or reprices an order.

Canonical `/api/...` endpoints use the V0.3 response envelope:

```json
{"success": true, "data": {}, "error": null, "trace_id": "..."}
```

Legacy `/api/v1/...` endpoints remain available for compatibility and keep the
legacy response envelope:

```json
{"success": true, "code": "OK", "message": "ok", "data": {}, "trace_id": "..."}
```

For each implemented `/api/v1/...` business endpoint, the matching `/api/...`
alias reuses the same handler and returns the same core `data`.

No endpoint below performs real trading. No endpoint requires or returns a real API key.

The iFinD P0 Shadow endpoints under `/api/workbench/realtime/*` are read-only and
default to `SHADOW` plus disabled. They use the existing provider adapter only;
the browser never calls iFinD directly. `/api/data-sources/ifind/usage` exposes
sanitized call counts and status, never credentials or raw responses.

Historical Workbench endpoints include `GET /api/workbench/available-dates`, `GET /api/workbench/runs`, `POST /api/workbench/runs/reconcile`, `POST /api/workbench/runs/load-existing`, and the run-bound result endpoints under `/api/workbench`. Reconciliation writes relationship metadata only and never executes a pipeline stage.

The optional iFinD score overlay and post-close advice endpoints are Shadow/advisory only. `POST /api/workbench/ifind-enhancement/run-shadow` cannot alter the official Quant ranking. `POST /api/workbench/post-close-actions/run-fast` returns `WAITING_FOR_POST_CLOSE_RUN` before the close and never creates an order. Position imports require preview and confirmation.

Server-paginated Workbench responses use `items`, `page`, `page_size`, `total`, and `total_pages`. Page numbering starts at 1; filtering and stable sorting happen before slicing the requested page.

| Module | Method | Path | Purpose | Mock-only status | Writes database | Real trading involved |
| --- | --- | --- | --- | --- | --- | --- |
| Health | GET | `/health` | Backend health and safety state | Mock-only | No | No |
| System | GET | `/api/v1/system/config-summary` | Safe runtime config summary | Mock-only | No | No |
| Database | GET | `/api/v1/database/health` | Local database connectivity | Mock-only | No | No |
| Data Sources | GET | `/api/v1/data-sources/status` | Provider status summary | Mock Provider only | No | No |
| Data Sources | GET | `/api/v1/data-sources/mock/stocks` | Mock stock universe | Mock Provider only | No | No |
| Data Sources | GET | `/api/v1/data-sources/mock/quotes` | Mock quote snapshot | Mock Provider only | No | No |
| Quant | GET | `/api/v1/quant/scan` | Rule-based quant ranking | Mock data only | Optional when `persist=true` | No |
| Quant | GET | `/api/v1/quant/config` | Quant config summary | Mock-only | No | No |
| Quant | POST | `/api/pools/run-quant-real` | Manual AKShare/BaoStock quant Top500 debug run | Manual real data debug, no LLM | Optional when `save_to_db=true` | No |
| LLM | GET | `/api/v1/llm/status` | LLM Gateway status | Mock LLM only | No | No |
| LLM | POST | `/api/v1/llm/mock-chat` | Mock chat response | Mock LLM only | Optional usage log | No |
| LLM | GET | `/api/v1/llm/usage-summary` | Mock usage summary | Mock LLM only | No | No |
| Screening | GET | `/api/v1/screening/light/run` | Light screening over quant candidates | Mock LLM only | Optional when `persist=true` | No |
| Screening | GET | `/api/v1/screening/light/config` | Screening config | Mock LLM only | No | No |
| Committee | GET | `/api/v1/committee/run` | AI committee ranking | Mock LLM only | Optional when `persist=true` | No |
| Committee | GET | `/api/v1/committee/config` | Committee config | Mock LLM only | No | No |
| Order Price | GET | `/api/v1/order-price/plans` | Advisory order-price plans | Rule engine + mock data | Optional when `persist=true` | No |
| Order Price | GET | `/api/v1/order-price/config` | Order price config | Mock-only | No | No |
| Virtual Trading | POST | `/api/v1/virtual-trading/accounts/default` | Create AI Simulation account | Virtual only | Yes | No |
| Virtual Trading | POST | `/api/v1/virtual-trading/run-plans` | Simulate order-plan execution | Virtual only | Yes | No |
| Virtual Trading | GET | `/api/v1/virtual-trading/account` | Virtual account summary | Virtual only | No | No |
| Virtual Trading | GET | `/api/v1/virtual-trading/orders` | Virtual orders | Virtual only | No | No |
| Virtual Trading | GET | `/api/v1/virtual-trading/positions` | Virtual positions | Virtual only | No | No |
| Virtual Trading | GET | `/api/v1/virtual-trading/trades` | Virtual trades | Virtual only | No | No |
| Virtual Trading | POST | `/api/v1/virtual-trading/orders/{order_id}/cancel` | Cancel virtual order | Virtual only | Yes | No |
| Virtual Trading | POST | `/api/v1/virtual-trading/orders/{order_id}/reprice` | Reprice virtual order | Virtual only | Yes | No |
| Alerts | POST | `/api/v1/alerts/intraday/scan` | Mock intraday alert scan | Mock data only | Yes | No |
| Alerts | GET | `/api/v1/alerts/recent` | Recent alerts | Mock data only | No | No |
| Alerts | GET | `/api/v1/alerts/config` | Alert config | Mock-only | No | No |
| Recheck | POST | `/api/v1/recheck/pre-market/run` | Pre-market advisory recheck | Mock data only | Yes | No |
| Recheck | POST | `/api/v1/recheck/order-plans/{order_plan_id}` | Recheck one advisory order plan | Mock data only | Yes | No |
| Recheck | GET | `/api/v1/recheck/config` | Recheck config | Mock-only | No | No |
| Review | POST | `/api/v1/review/daily/run` | Daily review generation | Mock LLM optional | Yes | No |
| Review | GET | `/api/v1/review/daily/{review_date}` | Daily review lookup | Mock-only | No | No |
| Review | POST | `/api/v1/review/predictions/evaluate` | Prediction evaluation | Mock-only | Yes | No |
| Review | POST | `/api/v1/review/order-plans/evaluate` | Order-plan evaluation | Mock-only | Yes | No |
| Review | GET | `/api/v1/review/config` | Review config | Mock-only | No | No |
| Memory | POST | `/api/v1/memory/notes` | Create memory note | Local database only | Yes | No |
| Memory | POST | `/api/v1/memory/search` | Search local memory | Local database only | Optional retrieval log | No |
| Memory | GET | `/api/v1/memory/notes/{note_id}` | Memory note lookup | Local database only | No | No |
| Memory | POST | `/api/v1/memory/links` | Link memory notes | Local database only | Yes | No |
| Memory | POST | `/api/v1/memory/reflection/from-review/{review_id}` | Reflection memory from review | Mock/local only | Yes | No |
| Memory | POST | `/api/v1/memory/playbook/from-review/{review_id}` | Playbook from review | Mock/local only | Yes | No |
| Memory | GET | `/api/v1/memory/playbooks` | Playbook list | Local database only | No | No |
| Memory | GET | `/api/v1/memory/config` | Memory config | Local database only | No | No |
| Config | GET | `/api/v1/config/effective` | Effective config | Mock-only safe config | No | No |
| Config | GET | `/api/v1/config/editable` | Editable config whitelist | Mock-only safe config | No | No |
| Config | PUT | `/api/v1/config/values/{config_key}` | Persist one whitelisted config | Safe whitelist only | Yes | No |
| Config | POST | `/api/v1/config/bulk` | Persist whitelisted config values | Safe whitelist only | Yes | No |
| Config | POST | `/api/v1/config/values/{config_key}/reset` | Reset config override | Safe whitelist only | Yes | No |
| Config | GET | `/api/v1/config/history` | Config change history | Safe whitelist only | No | No |
| iFinD Enhancement | POST | `/api/workbench/ifind-enhancement/run-shadow` | Calculate and persist Top100 Shadow overlay | Real cached Shadow data or baseline fallback | Yes | No |
| iFinD Enhancement | GET | `/api/workbench/ifind-enhancement/latest` | Latest Shadow A/B summary | Read-only | No | No |
| Post-close Actions | POST | `/api/workbench/post-close-actions/run-fast` | Generate rule-only advisory snapshot after close | No LLM | Yes | No |
| Post-close Actions | POST | `/api/workbench/post-close-actions/run-pro-review` | Queue conservative Gateway review | Optional Gateway | Yes | No |
| Post-close Actions | GET | `/api/workbench/post-close-actions/status` | Read run status by run or trade date | Read-only | No | No |
| Post-close Actions | GET | `/api/workbench/post-close-actions/results` | Paginated held/candidate advice | Read-only | No | No |
| Post-close Actions | GET | `/api/workbench/post-close-actions/history` | Immutable advice versions | Read-only | No | No |
| Post-close Actions | GET | `/api/workbench/post-close-actions/compare` | Baseline versus iFinD Shadow action comparison | Read-only | No | No |
| Post-close Actions | POST | `/api/workbench/post-close-actions/export` | Export centered four-sheet workbook | Local output only | No | No |
| Positions | GET | `/api/workbench/positions/current` | Current confirmed position snapshots | Read-only | No | No |
| Positions | POST | `/api/workbench/positions/import-preview` | Validate CSV/XLSX position file | Local validation | Yes | No |
| Positions | POST | `/api/workbench/positions/import-confirm` | Confirm immutable position snapshot | Local database | Yes | No |
| Positions | GET | `/api/workbench/positions/truth-status` | Read confirmed-position/confirmed-empty gate status | Read-only | No | No |
| Positions | POST | `/api/workbench/positions/confirm-empty` | Explicitly confirm immutable empty-position facts | Local database | Yes | No |
# Selection Performance Analytics V1

`/api/workbench/performance/*` 提供不可变选股 Cohort、个股/组合收益、缓存状态、增量刷新、失效、设置和四表 Excel 导出。正式查询只读取数据库；本阶段不调用 LLM、外部行情 API 或交易模块。完整契约见 `docs/SELECTION_PERFORMANCE_ANALYTICS_V1.md`。
# 午间推荐

午间推荐是独立的只读建议流水线。正式前端只在 `11:32-12:50` 发起，行情特征截止到上午收盘；历史验证必须显式开启，且不会被前端默认发送。所有结果均为 `actionable=false`，不会创建订单。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/workbench/midday/run` | 异步排队正式午间运行 |
| GET | `/api/workbench/midday/status` | 按运行编号或交易日查询阶段与计数 |
| GET | `/api/workbench/midday/results` | 分页读取午间评分、建议和规则价格 |
| GET | `/api/workbench/midday/history` | 查询历史运行 |
| POST | `/api/workbench/midday/recheck` | `13:01-13:10` 规则复核，不调用 LLM |
| POST | `/api/workbench/midday/export` | 导出五页居中 Excel |
| GET | `/api/workbench/midday/methodology` | 查看基线、影子增强和安全边界 |

正式运行复用最近兼容的上一交易日 Tushare Quant Top100，合并人工池、确认持仓与有效计划，最多 120 只。iFinD 仅形成独立午间影子增量，不回写正式 Quant 排名。
# 买入准入影子分析

- `POST /api/workbench/entry-timing/run-shadow`：显式确认后运行本地影子分析。
- `GET /api/workbench/entry-timing/latest?trade_date=YYYY-MM-DD`：读取当日最新运行摘要。
- `GET /api/workbench/entry-timing/results?run_id=...`：分页读取逐股分项得分与准入状态。
- `GET /api/workbench/entry-timing/methodology`：读取 V1 评分与准入方法说明。

该组接口不调用 LLM 或外部行情 API，不改变正式推荐结果。
# Entry Timing V2.1 Shadow

- `POST /api/workbench/entry-timing/v2/run-shadow`：显式确认后运行单日 V2.1 本地影子分析。
- `GET /api/workbench/entry-timing/v2/latest?trade_date=YYYY-MM-DD`：读取指定日期最新 V2.1 摘要。
- `GET /api/workbench/entry-timing/v2/results`：分页读取逐股结果，支持策略、情绪、市场状态、V1/V2 准入状态和候选池筛选。
- `GET /api/workbench/entry-timing/v2/methodology`：读取预注册版本、权重、策略阈值和影子安全策略。

### Entry Timing V2.2 Shadow

- `POST /api/workbench/entry-timing/v22/historical-shadow`：显式确认后，以本地数据库和缓存执行 V2.2 历史影子回放并导出 Excel。
- `GET /api/workbench/entry-timing/v22/latest?trade_date=YYYY-MM-DD`：读取指定日期最新的市场状态、部署、集中度和触发摘要。
- `GET /api/workbench/entry-timing/v22/results`：分页读取逐股结果，支持市场状态、部署状态、行业拥挤、盘中触发和候选池筛选。
- `GET /api/workbench/entry-timing/v22/methodology`：读取 V2.2 预注册规则、版本和安全边界。

以上端点不调用 LLM 或外部数据源，不创建订单，也不改变正式推荐。
