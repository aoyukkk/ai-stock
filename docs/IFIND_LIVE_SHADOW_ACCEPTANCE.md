# iFinD P0 Live Shadow 验收

本阶段用于验证 `IFindHttpP0Provider` 的只读实时能力。Tushare 仍是生产数据源，iFinD 只写入 Shadow 表和验收审计表；验收不会调用 LLM、量化、Flash、Pro、挂单、持仓或交易模块。

## 两种模式

- **CLOSED_SESSION_ACCEPTANCE**：收盘后执行一次，验证最新已完成交易日、指数日线与快照、最多 20 只股票快照、最多 3 只股票分钟线、缓存命中、数据库新会话回读和 Tushare 双源对比。
- **OPEN_SESSION_ACCEPTANCE**：下一个正常交易日盘中手动执行，默认 3 轮、每轮间隔至少 60 秒，分钟线只在最后一轮取样。不启用调度器。

盘后执行闭市验收示例：

```powershell
conda run -n ai-stock-agent python scripts/run_ifind_shadow_acceptance.py `
  --mode closed-session --trade-date latest-completed --pipeline-run latest-compatible `
  --stock-limit 20 --minute-stock-count 3 --max-external-calls 20 `
  --real-ifind --force-provider-refresh
```

盘中执行示例：

```powershell
conda run -n ai-stock-agent python scripts/run_ifind_shadow_acceptance.py `
  --mode open-session --trade-date latest-completed --stock-limit 20 `
  --minute-stock-count 3 --rounds 3 --interval-seconds 60 `
  --max-external-calls 40 --real-ifind --force-provider-refresh
```

`--real-ifind` 只在当前进程临时打开验收门禁，命令结束后恢复环境变量；不会修改 `.env` 或生产配置。执行前必须通过刷新令牌、HTTP 开关、Shadow 集成模式、数据库可写、调度关闭、实盘关闭和 Provider Registry 检查。任何门禁失败都不发起真实数据调用。

## 结果与晋级

报告位于 `data/reports/ifind/acceptance/`：

- `ifind_closed_session_acceptance_<timestamp>.json`
- `ifind_open_session_acceptance_<timestamp>.json`
- `ifind_dual_source_comparison_<trade_date>.json`
- `ifind_shadow_promotion_decision_<timestamp>.json`

报告只保存脱敏的状态、覆盖率、时效、哈希、计数和业务表不可变性结果，不保存令牌、请求头、原始响应或完整连接串。晋级决策只允许 `KEEP_SHADOW`、`PROMOTE_INDEX_ONLY`、`READY_FOR_SELECTED_POOL_MONITOR`、`BLOCKED` 之一，系统不会自动修改 Provider 配置或自动晋级。

当前默认保持：`ENABLE_REAL_TRADING=false`、iFinD production source=false、LLM calls=0、scheduler=false。
