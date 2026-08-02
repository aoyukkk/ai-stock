# 输出目录约定

所有可交付产物按交易日归档：

```text
outputs/
  YYYY-MM-DD/
    智能交易助手_YYYY-MM-DD.xlsx
    午盘/
      智能交易助手_午盘_YYYY-MM-DD.xlsx
      审计/
      历史版本/
      预览/
    审计/
    历史版本/
    预览/
    复盘/
      今日推荐复盘_截至YYYY-MM-DD.xlsx
      重点候选复盘_截至YYYY-MM-DD.xlsx
```

- 日期目录根部只放当天收盘后的最终人工阅读版。
- `午盘/` 保存当天唯一一版午盘分析推荐，文件名必须带“午盘”，不得覆盖收盘版。
- `审计/` 保存检查点、机器审计、测试结果和生成记录。
- `历史版本/` 保存完整机器版、旧工作簿和旧预览，不作为日常阅读入口。
- `预览/` 保存最终人工阅读版各工作表的视觉检查图片与公式检查结果。
- `复盘/今日推荐复盘_*` 只统计最终复核分达到配置门槛的当日推荐；`复盘/重点候选复盘_*` 保留同日全部重点候选。两者每天同时生成，人工与模型来源执行相同门槛规则。

人工阅读版统一使用中文标题、中文状态和中文说明。机器枚举、运行标识及完整审计字段只保留在 `审计/` 或 `历史版本/`。

## 默认制表规范

所有日常选股、午间推荐和盘后建议默认使用 `trading_assistant_human_v1`，无需用户重复指定：

1. 收盘版统一为 `智能交易助手_YYYY-MM-DD.xlsx`，放在交易日目录根部；午盘版统一为 `午盘/智能交易助手_午盘_YYYY-MM-DD.xlsx`。
2. 首页固定提供关键数量卡片和优先复核前十名。
3. 正文按“今日推荐、重点候选、价格与权重/挂单与仓位、复核依据/基本面摘要、量化前100、当前问题”排列。
4. “重点候选”保留完整最终候选；“今日推荐”默认仅保留最终复核分不低于60分的模型、人工或共同入选股票。没有达标股票时保留带表头的空推荐表。
5. 人工阅读内容使用中文，不展示内部枚举、请求哈希、模型原始推理或冗长声明。
6. 股票代码必须按文本保存为六位，保留前导零；表格数据默认水平和垂直居中。
7. 表头使用深蓝底白字，隐藏网格线，冻结表头，长文本换行，列宽和行高以首屏可读为准。
8. 不允许空数据行；暂缺字段使用简短中文状态，不制造看似有值的数据。
9. 机器明细、完整审计和旧版本进入 `审计/` 或 `历史版本/`，不干扰每日阅读入口。
10. 交付前必须逐页生成预览，并检查公式错误、压缩包完整性、表头与 Excel 表对象元数据一致性。
11. 任一结构检查失败时禁止覆盖当天正式文件，先修复后重新生成。

具体开关位于 `config/reporting.yaml`，生成后由 `reporting.workbook_standard` 执行统一验收。
## Ranking forward effectiveness (Shadow)

```text
outputs/ranking_evaluation/
  snapshots/<ranking-trade-date>/<factor-version>/
    rank-snapshot-<hash>.json
    rank-snapshot-<hash>.csv
  weekly/<week-ending>/<factor-version>/<run-id>/
    summary.json
    daily_metrics.csv
    ranking_details.csv
    data_quality.csv
    run_manifest.json
    量化排名前向效度评估_<factor-version>_<week-ending>_<run-id>.xlsx
```

快照和报告目录均由日期、版本、run_id/Hash 隔离，禁止覆盖。Excel 只在批准的
`@oai/artifact-tool` 运行时可用时生成；否则 manifest 明确记录
`workbook_status=RUNTIME_UNAVAILABLE`。
