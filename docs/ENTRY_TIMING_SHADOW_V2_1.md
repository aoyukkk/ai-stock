# Short-Term Trading Admission V2.1 Shadow

## 定位

V2.1 在 `TUSHARE_BASELINE_V1` 与 Entry Timing V1 之后增加确定性策略分类、市场情绪硬门禁、Entry Timing V2 分数和策略级准入阈值。它只生成独立、不可变的影子快照，不修改正式 Quant、Flash V4、Pro、推荐、挂单或仓位结果。

默认配置位于 `config/entry_timing_v2_1.yaml`。`enabled=false`、`shadow_only=true`，真实交易、虚拟订单、调度器、外部调用、LLM 调用和参数搜索均关闭。配置由 `EntryTimingV2ConfigService` 经统一 `AppConfig` 读取；本阶段仅登记新配置文件，没有通过运行时界面修改配置，因此没有新增 `config_history` 事件。

## 决策顺序

1. 读取已有 Quant 候选、V1 结果、本地行情缓存和本地市场快照。
2. `ShortTermStrategyClassifier` 归类为趋势突破、强势回踩、板块共振、超跌反弹或未分类。
3. `MarketEmotionEngine` 独立计算市场情绪；该分数不进入 Entry Timing 或准入排序加权。
4. `StrategyMarketEmotionGate` 根据策略与 GREEN/YELLOW/RED 状态执行硬门禁。
5. `EntryTimingV2Engine` 按 20/15/20/20/20/5 权重计算 V2 分数，缺失项按可用权重重归一化。
6. `StrategyAwareAdmissionEngine` 依次执行数据、既有风险、高位风险、策略分类、市场情绪、策略阈值、准入排序和数量限制。
7. 仅 PASS 可进入最多 20 只的影子池；不降阈值补足，人工挑战池始终隔离。

## 运行

单日影子运行：

```powershell
python scripts/run_entry_timing_v2_shadow.py --trade-date 2026-07-17
```

历史公平回放：

```powershell
python scripts/run_entry_timing_v2_shadow.py --historical-start 2026-07-13 --historical-end 2026-07-17
```

历史回放只读取本地数据库与已验证缓存，同时输出 Original、Entry Timing V1、Entry Timing V2.1。D+1、D+3、D+5 仅统计实际存在的后续交易日，缺失值不记为零。

## 审计

V2 使用独立的 `admission_v2_run`、`strategy_classification_result`、`market_emotion_snapshot` 和 `entry_timing_v2_result` 表。快照禁止更新和删除；输入哈希保证幂等。每次运行保存 Quant、Flash 和 Pro 的前后哈希，并记录 LLM、外部 API 和订单计数。

当前版本仅供 Shadow 验证。即使建议达到 `READY_FOR_FORWARD_SHADOW`，也不能自动进入正式推荐或执行交易。
