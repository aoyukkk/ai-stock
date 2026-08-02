# Quant Ranking Forward Effectiveness Evaluation V1

状态：`SHADOW`

本模块只评价“Quant 在进入 LLM 前的原始 Top100 排名”与未来收益是否存在稳定的正向关系。它不参与推荐、评分、调权、Prompt、订单、仓位或 Decision Snapshot。

首期前向窗口固定从 `2026-07-24` 的收盘排名开始：`2026-07-27`
为 D1，之后继续按 A 股合法交易日映射 D3、D5、D10。

## 固定合同

- `evaluation_version=RANKING_FORWARD_EFFECTIVENESS_V1`
- `evaluation_scope=QUANT_PRE_LLM_TOP100`
- `return_basis=RAW_CLOSE`
- 期限：D1、D3、D5、D10，均按缓存的 Tushare SSE `trade_cal` 映射
- Rank IC：`Spearman(101-original_rank, future_return)`
- 分组：G1=1–20、G2=21–40、G3=41–60、G4=61–80、G5=81–100
- 周累计：先算每个排名日，再对成熟排名日做等权算术平均
- Legacy 与 V2 必须显式传入 `factor_version`，禁止混合

## 每日运行

双击：

```text
run_ranking_evaluation_daily_once.cmd
```

或：

```powershell
conda run -n ai-stock-agent python scripts/run_ranking_evaluation_once.py --mode daily --as-of-date YYYY-MM-DD
```

每日入口只捕获指定日期已经成功持久化的受支持 Quant 版本，并增量扫描此前未成熟的 D1/D3/D5/D10。默认不导入 activation_date 之前的历史快照。

显式捕获单次运行：

```powershell
conda run -n ai-stock-agent python scripts/capture_ranking_snapshot.py --trade-date YYYY-MM-DD --source-quant-run-id RUN_ID --factor-version TUSHARE_BASELINE_V1
```

历史导入必须额外传入 `--allow-historical-import`，并标记为 `HISTORICAL_IMPORT`。

## 周五报告

双击：

```text
run_ranking_evaluation_weekly_once.cmd TUSHARE_BASELINE_V1
```

或：

```powershell
conda run -n ai-stock-agent python scripts/run_ranking_weekly_evaluation.py --week-ending YYYY-MM-DD --factor-version TUSHARE_BASELINE_V1
```

周五不是交易日时，`week_ending` 仍为周五，行情截止日使用不晚于该周五的最近合法交易日。

## 数据质量与不可变性

- 原始 100 行全部保留，重复、断档、缺失不会去重、补位或重排。
- 相同日期、版本、范围且 Hash 相同为幂等；Hash 不同返回 `RANKING_SNAPSHOT_IMMUTABLE_CONFLICT`。
- 停牌保留原 due date，状态为 `SUSPENDED_ON_DUE_DATE`，不得顺延。
- 成熟结果源发生变化时不覆盖，记录 `MATURED_OUTCOME_SOURCE_CHANGED`。
- `RAW_CLOSE` 与 adjusted return 分字段保存，Headline 指标只使用 `RAW_CLOSE`。
- Scheduler 固定关闭；模块失败只给正式 Quant 增加警告，不改变 Quant 成功状态。

## 输出

不可变快照：

```text
outputs/ranking_evaluation/snapshots/<trade-date>/<factor-version>/
```

周报：

```text
outputs/ranking_evaluation/weekly/<week-ending>/<factor-version>/<run-id>/
```

固定输出 `summary.json`、`daily_metrics.csv`、`ranking_details.csv`、`data_quality.csv`、`run_manifest.json`。Excel 仅通过批准的 `@oai/artifact-tool` 运行时生成，不允许回退到 openpyxl；运行时未加载时周报为 `PARTIAL_SUCCESS`，其他审计产物仍保留。

## API

- `POST /api/ranking-evaluation/capture`
- `POST /api/ranking-evaluation/refresh-outcomes`
- `POST /api/ranking-evaluation/run-weekly`
- `GET /api/ranking-evaluation/summary`
- `GET /api/ranking-evaluation/daily-metrics`
- `GET /api/ranking-evaluation/details`
- `GET /api/ranking-evaluation/data-quality`
- `GET /api/ranking-evaluation/runs`

所有查询必须显式传 `factor_version`；没有编辑、删除或改写排名的 API。
