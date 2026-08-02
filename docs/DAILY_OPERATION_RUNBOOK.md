# 每日常态化运行说明

## Full-Universe Quant Effectiveness（可选Shadow）

冻结全量Quant快照：

```powershell
conda run -n ai-stock-agent python scripts/capture_full_universe_quant_effectiveness.py --trade-date YYYY-MM-DD --quant-run-id RUN_ID --factor-version TUSHARE_QUANT_V2_CORRECTED_SHADOW --no-network
```

生成阶段报告：

```powershell
conda run -n ai-stock-agent python scripts/run_full_universe_quant_validation.py --start-date YYYY-MM-DD --end-date YYYY-MM-DD --as-of-date YYYY-MM-DD --factor-version TUSHARE_QUANT_V2_CORRECTED_SHADOW --no-network --skip-excel
```

也可双击 `run_full_universe_quant_effectiveness_once.cmd` 并传入日期、Quant Run ID和
factor version。Scheduler保持关闭。该流程不重算Quant、不调用LLM/搜索/外部Provider、
不创建订单。Excel不可用时查看同目录Markdown、JSON和CSV。

## Quant + Flash 前向效度 Shadow（2026-07-31 起）

业务 Quant、V2 Flash、V3 Event Overlay 结果全部持久化后，双击：

```text
run_model_stage_effectiveness_daily_once.cmd
```

该入口只读取冻结结果，先幂等采集当日 Quant/V2/V3 阶段快照，再复用现有
D1/D3/D5/D10 收益回填，最后更新研究全样本和开盘前可执行样本的 Daily Metric。
它不会调用 LLM、不会联网补历史事件、不会创建订单，也不会启动 Scheduler。评价
失败只写入 WARNING/数据质量，不改变原业务 Run 状态。

每周五需要累计报告时，双击：

```text
run_model_stage_effectiveness_weekly_once.cmd
```

按提示输入 `week_ending`、Quant factor version 和 screening version。V2 和 V3
必须分开运行，禁止省略版本或混合查询。标准 V2 版本为
`FLASH_V2_STRUCTURED_LIGHT_SCREENING_V5`；V3.1 为
`LLM_SCREENING_V3_1_FRESH_EVENT_OVERLAY_SHADOW`。报告按排名日等权，Bootstrap 的抽样单位
也是排名日。Excel 只在批准的 artifact-tool runtime 可用时生成；否则查看同目录的
`cumulative_summary.json`、五个 CSV、`run_manifest.json` 和
`validation_report.md`。

本文档是项目每日运行的唯一操作基准。所有命令固定使用 Conda 环境 `ai-stock-agent`；系统只生成分析、价格计划和仓位建议，不连接券商、不创建真实或虚拟订单。

根目录唯一推荐人工入口为 `双击运行_每日工作流程.bat`；盘后脚本入口为 `scripts/run_v2_postclose_official_once.py`，其内部固定追加 V3.1 Shadow。`scripts/SCRIPT_CLASSIFICATION.yaml` 中标为 `SHADOW_EXPERIMENT`、`HISTORICAL_ONE_OFF`、`MIGRATION`、`MAINTENANCE` 或 `UNKNOWN_REVIEW_REQUIRED` 的脚本均不是每日正式入口；保留它们仅用于兼容和审计。

### V3.1 Event Overlay Shadow（正式日常 Shadow 阶段）

V3.1 已纳入 `run_close_once.cmd` 的正式日常编排，运行位置固定在 V2 Quant、Flash/Pro、
基本面补全、正式工作簿和数据库提交之后。它仍是 Shadow，不替代 V2 正式推荐、不修改
Quant 分项、不创建订单。需要单独恢复时可双击：

```text
run_v3_event_overlay_shadow_once.cmd
```

高级命令行入口：

```powershell
conda run --no-capture-output -n ai-stock-agent python scripts/run_v3_event_overlay_shadow_once.py --trade-date YYYY-MM-DD --daily-real-search
```

固定正确流程如下：

1. 从 `v2_full_universe.csv` 按 Quant 排名选取 Hard Gate 后前 100，只在不足时继续向后补足；Hard Gate 股票不得进入搜索。
2. 每只股票只调用一次结构化最新事件搜索，一次覆盖业绩、订单、并购、处罚、事故、产品价格、解禁、减持和质押；禁止按 Quant 分项重复搜索或由 LLM 重算 Quant。
3. 先运行 5 只真实 Canary，必须 5/5 成功、真实使用 Web Search、输入 Hash 和 Prompt/合同版本一致，才允许运行 Top100。
4. 市场新闻限 36 小时、普通公司事件限 72 小时、Tier 1 正式公告限 168 小时；未来信息、未知发布时间、缺少原始 URL、过期内容、重复转载和非法数值只保留审计，不参与评分。
5. 无合格证据时 `event_delta=0`，V3.1 分必须严格等于 Quant 分；不得用固定中性分、旧新闻或 LLM 推测补空。
6. 单股事件调整绝对值最多 8 分；`WATCH_ONLY` 不得因正面事件加分，`BLOCK` 只来自已定义风险动作，LLM 不直接决定最终排名、价格或仓位。
7. 搜索工具中间轮必须保留原消息和工具合同；最终 JSON 非法时只允许一次可审计的语法修复续轮，禁止新增或替换证据，第二次失败即 Fail Closed。
8. Checkpoint 绑定交易日、股票代码、因子版本、输入 Hash、Prompt、搜索合同、来源策略和结果 Hash。重跑复用成功项，只补失败项；输入或合同变化必须判为 stale。
9. 只有完整 100/100、搜索失败 0、Provider 失败 0、Hard Gate 违规 0 且 `database_publish_eligible=true` 时才写入业务库并原子同步 Web。Canary、Mock、离线和失败运行禁止发布。
10. 真实搜索的实际执行时间和决策时点都必须早于目标交易日 09:25；到达截止时间后只能复用已冻结成功结果，禁止伪装成早前时点重新搜索。

幂等规则：同一 V2 源文件、全量 Universe、Prompt 和合同已有完整成功 Top100 时直接复用，
真实网络调用为 0；仅有成功 Canary 或部分失败 Full 时沿用相同决策时点和成功 Checkpoint，
只重试失败股票。输出位于 `outputs/event_overlay/<trade_date>/<run_id>/`，不覆盖 V2 工作簿。
网页只读展示 V3.1 结果，没有人工改分或生产晋级入口。

## 每日双击时间表

项目不创建 Windows 定时任务，也不会自动启动。每天双击根目录的：

```text
双击运行_每日工作流程.bat
```

| 建议时间 | BAT 选项 | 执行内容 | 主要产物 |
|---|---:|---|---|
| 交易日 11:30 左右 | `1` | 常规午盘推荐 | 午盘工作簿和午盘审计 |
| 交易日 17:00 以后 | `2` | 正式盘后全 A + V3.1 事件 Shadow | 正式日线、近 7 交易日对比、市场复盘、候选复盘、V3.1 与审计 |

选择 `Q` 会直接退出，不启动任何流程。BAT 最终调用 `run_midday_once.cmd` 或 `run_close_once.cmd`，二者都固定使用 Conda 环境 `ai-stock-agent`。

## 固定安全条件

- `.env` 中 `ENABLE_REAL_TRADING=false`。
- 全 A Quant 的 LLM 调用数必须为 0。
- Flash 和 Pro 必须经过 LLM Gateway，不能用 Mock 冒充正式结果。
- 挂单价和仓位由本地规则计算，LLM 只能筛选、复核和解释。
- 不自动调参、不降低阈值凑候选、不覆盖历史工作簿。
- 同一交易日已有正式成功/部分成功盘后运行时，重复执行返回 `REUSED_EXISTING_OFFICIAL_RUN`。

## 11:30 常规午盘

双击 `双击运行_每日工作流程.bat` 并选择 `1`。高级命令行入口：

```powershell
run_midday_once.cmd
```

入口固定执行 `conda run -n ai-stock-agent`。11:30 启动时会等待至 11:32，再完成以下门禁：

1. 最近完整交易日 Quant Top100 可用。
2. iFinD 只读 Shadow 认证和已验证接口可用。
3. LLM Gateway 可用。
4. 预计 Provider 调用不超过 30 次。
5. `ENABLE_REAL_TRADING=false`。

午盘复用同日同输入的已有结果，不重复抓行情、不重复调用 LLM、不覆盖打开中的工作簿。预检命令：

```powershell
run_midday_once.cmd --preflight-only --no-wait
```

2026-07-20 的 iFinD 全 A 午盘雷达属于专项审计：覆盖 98.8282%，但实际额度消耗 1,701,498，超过 1,000,000 的单次安全阈值。因此双击入口只运行现有 Top100/分级拉取安全路径；专项脚本保留在 `scripts/` 供历史审计，不作为日常入口。

## 17:00 正式盘后

双击 `双击运行_每日工作流程.bat` 并选择 `2`。高级命令行入口：

```powershell
run_close_once.cmd
```

指定日期：

```powershell
run_close_once.cmd --trade-date YYYY-MM-DD
```

正式顺序固定为：

1. 读取交易日历并选择最近成功正式工作簿作为样式参考。
2. 校验 Tushare `daily`、`daily_basic`、`moneyflow`、`stk_limit`、`adj_factor` 的交易日期、去重、字段完整度和覆盖率。
3. 未通过时只补取未通过的数据集，每 5 分钟复查，最晚至 19:00；门禁失败不进入 LLM。
4. 执行全 A Quant，按日批量缓存，禁止逐股 API 调用和 Quant LLM 调用。
5. 读取当天明确人工池；空人工池保持为空，不虚构、不自动继承。
6. 执行真实 Flash、Pro、市场复盘和规则价格/仓位建议。
7. 先提交数据库最终状态并回读，再生成正式 Excel/JSON/Markdown。
8. 生成滚动近 7 个交易日对比、今日推荐复盘和重点候选复盘。
9. 运行工作簿结构、公式、股票代码和 Excel 兼容性检查。
10. 执行 V3.1：复用同源成功结果，或按“5只 Canary → Hard-Gate-free Top100”进行结构化最新事件搜索。
11. 验证无合格证据的股票分数未变化、全部证据时效合法、失败数为 0，再将完整 V3.1 结果原子同步到 Web。

盘后报告文件名按实际交易日动态生成，不再写死 `2026-07-20`。参考模板也会递归查找上一交易日的 `正式日线/` 目录，避免第二天找不到前一日模板。

## 每日输出

```text
outputs/YYYY-MM-DD/
  午盘/
    智能交易助手_午盘_YYYY-MM-DD.xlsx
    审计/午盘一键运行报告.json
  正式日线/
    智能交易助手_YYYY-MM-DD.xlsx
    postclose_official_YYYY-MM-DD_<run>.json
    postclose_official_YYYY-MM-DD_<run>.md
    postclose_official_audit_YYYY-MM-DD_<run>.json
    近7交易日数据对比_<start>_至_YYYY-MM-DD.xlsx
  复盘/
    大盘复盘_YYYY-MM-DD.xlsx
    今日推荐复盘_截至YYYY-MM-DD.xlsx
    重点候选复盘_截至YYYY-MM-DD.xlsx
  event_overlay/<run_id>/
    V3事件覆盖层Shadow_YYYY-MM-DD_<run_id>.xlsx
    run_manifest.json
    event_evidence_items.csv
    event_evidence_snapshot.json
    validation_report.md
```

2026-07-20 的“上周每日选股统计”保存在 `outputs/2026-07-20/复盘/`，一个 Excel 内含汇总页和每个选入日的独立工作表，字段包括选入日期、每日盈亏、最终盈亏和截至评价日最高点盈亏。日常盘后则固定生成滚动近 7 交易日对比，历史日期缺失时保留空值并标注，不重新调用历史 LLM。

## 工作簿兼容性防线

所有新工作簿必须满足：

- 首页只能显示数据库回读后的最终状态，不能显示 `RUNNING`。
- 股票代码按文本保存，保留前导 0。
- 公式错误扫描为 0。
- 表格筛选器与 Excel Table 不重叠。
- 冻结窗格只保留一个合法视图选择，避免 Excel 打开时提示修复。
- 继承最近成功正式工作簿的 Sheet 顺序、表头和视觉规范。
- 全部用户可见工作表完成结构检查和视觉抽查后再交付。

共享实现位于 `reporting/workbook_style.py`，不得在各脚本内复制一套互相冲突的格式逻辑。

## 失败恢复

先查看当天审计和日志。盘后任务具备阶段检查点，直接重复运行通常会复用已完成 Quant/Flash/Pro：

```powershell
run_close_once.cmd --trade-date YYYY-MM-DD
```

需要人工定位旧常态化流程阶段时：

```powershell
conda run -n ai-stock-agent python scripts/run_daily_routine.py status --trade-date YYYY-MM-DD
conda run -n ai-stock-agent python scripts/run_daily_routine.py close --trade-date YYYY-MM-DD --start-stage pro --confirm-llm-budget
```

上述命令仅用于旧阶段兼容恢复，不会替代正式 V2+V3.1 盘后入口。V3.1 单独恢复使用：

```powershell
conda run --no-capture-output -n ai-stock-agent python scripts/run_v3_event_overlay_shadow_once.py --trade-date YYYY-MM-DD --daily-real-search
```

不要因导出或格式问题重跑 Flash/Pro。修复后应从 `market`、`export` 或 `monitor` 阶段续跑；V3.1 重跑必须复用成功 Canary/Checkpoint，只补失败股票。

## 周度统计

正式盘后每天都会生成滚动 7 交易日对比。需要周一至周五固定三份周报时执行：

```powershell
conda run -n ai-stock-agent python scripts/build_weekly_outputs.py --week-start YYYY-MM-DD --week-end YYYY-MM-DD
```

该命令只读取已落库候选和本地批量行情，不重跑历史 Quant、Flash 或 LLM。

## 每日验收

```powershell
conda run -n ai-stock-agent python scripts/check_security_config.py
conda run -n ai-stock-agent python -m pytest -q
conda run -n ai-stock-agent python -m compileall . -q
```

同时确认：

- BAT 最终显示 `Workflow completed successfully`；失败时记录退出码。
- 当天午盘或盘后审计文件存在。
- Tushare 五类批量数据日期一致，`per_stock_api_call_count=0`。
- 正式工作簿和近 7 交易日对比均可直接由 Excel 打开。
- `ENABLE_REAL_TRADING=false`、真实订单 0、虚拟订单 0、项目 `Scheduler=false`。
- V3.1 完整运行 `input_count=100`、`successful_evaluation_count=100`、`search_failure_count=0`、`provider_failure_count=0`。
- `hard_gate_integrity.input_hard_gate_count=0`，且所有无合格证据股票 `v3_screening_score == quant_score`。
- Web 业务快照 `PRAGMA integrity_check=ok`，V3.1 明细/快照/复核数量分别为 `100/100/100`，Top20 为 20。

## 目录保留与清理规则

- 必须保留：`data/`、`outputs/`、`backups/`、`release/`、`.env` 和配置文件。
- 可直接重建：`build/`、`.pytest_cache/`、`__pycache__/`、`.pyc`、`tmp/`。
- 过时但仍有审计价值的说明移动到 `docs/archive/`，不再放在根目录与当前说明混用。
- 根目录用户入口为 `双击运行_每日工作流程.bat`；`run_midday_once.cmd`、`run_close_once.cmd` 是 BAT 调用的稳定底层入口。日期写死的一次性命令移入备份归档。
## Quant 原始排名前向效度（Shadow，可选）

正式 Quant 的原始 Top100 持久化后会以非阻断方式尝试冻结排名快照。评估失败只记录
`RANKING_EVALUATION_CAPTURE_FAILED`，不得把正式 Quant 的成功状态改成失败。

每日增量冻结与收益回填可双击：

```text
run_ranking_evaluation_daily_once.cmd
```

周五按版本生成累计报告：

```text
run_ranking_evaluation_weekly_once.cmd TUSHARE_BASELINE_V1
run_ranking_evaluation_weekly_once.cmd TUSHARE_QUANT_V2_CORRECTED_SHADOW
```

Scheduler 保持关闭；不要省略 `factor_version`，不要把 Legacy 与 V2 混合查询。详细合同见
`docs/RANKING_FORWARD_EFFECTIVENESS_V1.md`。
