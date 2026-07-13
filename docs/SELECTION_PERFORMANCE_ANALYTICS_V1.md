# 选股收益统计 V1

## 定位

选股收益统计衡量不可变历史候选集合入选后的市场价格表现，不代表实际成交收益、账户盈亏或交易建议。功能保持 `ENABLE_REAL_TRADING=false`、scheduler disabled、零 LLM、零订单，并且不修改 Quant、Flash、Pro、挂单价或仓位算法。

## 数据与流程

```text
已完成 Pipeline Run
  -> 不可变 Selection Cohort / Member 快照
  -> TradeCalendarService 解析交易日
  -> stock_market_data 或 Tushare trade_date daily 本地批量缓存
  -> 个股日收益与复合收益
  -> 等权或历史建议仓位组合收益
  -> 数据库存储正式结果
  -> API / CenteredDataTable / 四表 Excel
```

缓存分为三层：Raw Market Cache 按交易日保存可替换的原始行情；Selection Snapshot 长期保存且不可修改；Performance Result Cache 是带输入 Hash、状态、行数与 checksum 的可重建查询加速层。API 始终从数据库读取正式结果。

## 收益口径

- `NEXT_OPEN`：首日 `close / next_open - 1`，后续优先使用已验证 `pct_chg`。
- `SIGNAL_CLOSE`：首日 `close_D1 / selection_close - 1`，只用于信号评价。
- 累计收益：`product(1 + daily_return) - 1`。
- 回撤：`current_value / historical_peak_value - 1`。
- 确认停牌：收益为 0，状态为 `SUSPENDED_CARRY_FORWARD`。
- 未知缺失：收益为 null，不伪装成 0，并降低覆盖率。

## 缓存与增量

输入 Hash 包含算法/Schema 版本、选择日期范围、评价截止日、lookback、收益口径、选股范围、加权方式、零仓位/风险阻断开关、Pipeline/Candidate/Quant/Position 标识、行情水位、交易日历版本与配置快照 Hash。只有完全一致的 `SUCCESS` 可直接复用。

新完整行情日会令旧结果显示 `STALE`。增量刷新在原 Run 上只追加新的评价日期，唯一约束阻止重复；行情更正、口径变化、Cohort变化、算法变化或 checksum 异常会要求失效或重算。文件缓存采用临时文件、完整性检查、checksum 和原子替换。

## API

- `POST /api/workbench/performance/run`
- `GET /api/workbench/performance/summary|cohorts|daily|stocks|runs|cache-status|methodology`
- `POST /api/workbench/performance/incremental-refresh|invalidate|export`
- `GET/PUT /api/workbench/performance/settings`

数据更新接口完成后以事件方式启动默认收益刷新；不启用全局 scheduler。

## 前端与 Excel

路由为 `/selection-performance`。页面提供 5/10/20/60 日快捷范围、自定义日期、两种收益口径、六种选股范围、两种加权方式、缓存状态、统计卡片、两张轻量图、三张 `CenteredDataTable` 与个股详情抽屉。

Excel 文件名为 `selection_performance_<evaluation_end_date>_<lookback>td.xlsx`，包含选股日汇总、组合每日涨跌、个股收益明细、口径与异常四张表。所有表格水平/垂直居中、自动换行，股票代码使用文本格式，收益使用百分比格式。

## 验证

```powershell
conda run -n ai-stock-agent python -m pytest -q
conda run -n ai-stock-agent python scripts/check_security_config.py
conda run -n ai-stock-agent python -m compileall -q .
cd frontend
npm run test
npm run typecheck
npm run build
```
