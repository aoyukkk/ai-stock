# iFinD P0 Shadow Integration

本阶段只接入此前验证通过的 iFinD HTTP 能力：指数日线、指数实时快照、股票实时快照和 1 分钟数据。统一模式为 `SHADOW`，默认关闭，Tushare 仍是 A 股正式日线、财务、历史和量化数据源。

实时页面 `/realtime-monitor` 只读展示最终候选、人工池、持仓和挂单计划的去重监测池。它不自动下单、撤单、改价，不改量化分数、Flash/Pro 结果、挂单价、仓位建议或正式大盘复盘结论；后端没有调度器，自动刷新只在页面可见且用户主动开启时运行。指数日线和指数实时快照都只进入 Shadow 对比区。

Shadow 数据写入 `index_market_daily`、`market_snapshot`、`market_minute_bar`，外部调用统计写入 `external_provider_usage`。所有写入都带用途，`PRODUCTION_INPUT` 会被拒绝。审计记录只保留提供方、能力、批量数量、延迟、缓存和错误类别，不保存 Token、请求头或原始响应。

本阶段不重复发起真实 iFinD 校准请求，沿用既有验证结果：实时批量 4 只、指数批量 2 个、分钟批量 1 只；新增服务通过 Fake Provider 测试验证批次、缓存和幂等行为。
