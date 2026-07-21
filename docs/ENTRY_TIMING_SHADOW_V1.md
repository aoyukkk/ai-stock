# 买入准入影子层 V1

## 定位

买入准入层对已经完成的候选池做 1 至 10 个交易日尺度的入场时机复核。当前版本仅为 Shadow：默认关闭、不进入正式推荐、不修改 Quant、Flash V4 或 Pro 结果，也不会创建订单。

## 评分与准入

总分 100 分，由价格位置 25、回撤质量 20、量价结构 20、板块共振 15、市场适配 10、流动性 10 组成。权重、数据质量门槛和准入门槛来自 `config/entry_timing.yaml`。

- `PASS`：时机分达到 70，且 Quant、风险及市场硬门槛通过。
- `REVIEW`：时机分 60 至 69，或因高位风险、红色市场环境被封顶。
- `BLOCK`：时机分低于 60，或 Quant / 风险硬门槛失败。
- `DATA_INSUFFICIENT`：本地缓存覆盖率不足。

正式准入只取 `PASS`，最多 20 只；不足 20 只时保持实际数量，禁止降低阈值补足。人工候选固定进入 `MANUAL_CHALLENGE_POOL`，不混入模型候选表现。

## 数据与审计

分析只读取本地 Tushare `trade_date` 缓存和数据库中的既有候选、市场快照、股票主数据。每次运行记录输入哈希、配置快照、Quant 前后哈希、分项得分、风险标签、阻断原因及调用计数。运行记录与逐股结果均为不可变快照。

Flash V5 仅提供新的短线评分契约和 Prompt 快照，本阶段不调用 LLM。Flash V4 和正式推荐链路保持不变。

## 使用

前端进入“买入准入分析”，选择交易日后先读取已有结果。需要生成时，点击“运行影子分析”并确认。

命令行单日运行：

```powershell
python scripts/run_entry_timing_shadow.py --trade-date 2026-07-17
```

零网络历史回放：

```powershell
python scripts/run_entry_timing_shadow.py --historical-start 2026-07-13 --historical-end 2026-07-17
```

当日存在影子结果时，日常 Excel 会追加 `08_买入准入分析`；不存在时不会增加空工作表，也不会覆盖旧文件。
