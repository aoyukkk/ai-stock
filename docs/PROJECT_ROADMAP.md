# AI Stock Agent 当前规划与实施状态

状态基准日期：2026-07-22
适用目录：`D:\ai stock agent`
本文件是当前项目规划和状态的唯一基准。日常执行仍以 `DAILY_OPERATION_RUNBOOK.md` 为准；历史设计以 `archive/` 为准。

## 1. 状态定义

| 状态 | 含义 |
|---|---|
| `OPERATIONAL` | 已实现、已验证，可在现有安全边界内手工运行。 |
| `CONDITIONAL` | 主流程可用，但仍依赖人工确认、数据水位、额度或外部服务。 |
| `SHADOW` | 已实现并落库，只做观察和评价，不影响正式推荐。 |
| `EXPERIMENTAL` | 隔离研究，不接入正式推荐、排名或交易。 |
| `PARTIAL` | 已完成部分能力，但尚未形成稳定端到端闭环。 |
| `BLOCKED` | 当前有明确门禁，不能按目标范围继续。 |
| `DISABLED` | 代码可能存在，但配置明确关闭。 |
| `ARCHIVED` | 仅用于历史追溯，不代表当前实现。 |

“存在代码、表或页面”不等于“已晋级生产”。所有生产状态以配置、安全门禁、最近运行结果和晋级结论共同判断。

## 2. 当前总判断

- 系统定位仍是 A 股只读决策辅助工具，最终交易决策由人工负责。
- 正式量化基线为 `TUSHARE_BASELINE_V1`，五大权重保持 `25/25/20/15/15`。
- 正式技术价格口径仍为 `RAW`；QFQ/HFQ 时点调整和涨跌停风险子因子只完成 A/B 与校准，未成为生产默认值。
- Entry Timing V1、V2.1、V2.2、Admission V3、Decision Explainability 和 Forward Shadow 均未晋级正式推荐。
- 当前 Shadow 晋级结论为 `KEEP_V22_V3_SHADOW`。
- 大盘次日方向 GRU 实验结论为 `WEAK_OR_UNSTABLE_EDGE`，不得接入正式模型。
- `ENABLE_REAL_TRADING=false`，项目 Scheduler 关闭，虚拟交易和纸面交易配置关闭，内部 Web 服务默认关闭。
- 当前代码基线已通过 940 个 Python 测试、15 个前端测试、前端 typecheck/build、compileall 和安全检查。
- 本状态包含当前工作区中尚未提交的 Forward Shadow 与市场方向实验实现；最后已提交版本仍为 `8efc55a`。

## 3. 各子项目实际状态

| 子项目 | 当前状态 | 已实现情况 | 当前限制或下一道门禁 |
|---|---|---|---|
| 安全边界 | `OPERATIONAL` | 实盘、虚拟交易、纸面交易和 Scheduler 默认关闭；安全检查通过。 | 必须持续保持关闭；任何交易能力扩展都需单独授权和合规评审。 |
| Tushare 日线数据 | `OPERATIONAL` | 已有交易日批量缓存、时点校验、数据水位和全A量化输入。 | 正式盘后仍需五类数据同日、去重、覆盖率和可用时间全部通过。 |
| iFinD P0 实时数据 | `SHADOW` | 已实现只读 Provider、闭市/开市验收、指数/快照/分钟线和双源对比。 | 仍不是生产数据源；全A高额度路径存在配额安全问题。 |
| 全A Quant Baseline | `OPERATIONAL` | `TUSHARE_BASELINE_V1`、25/25/20/15/15、批量缓存、零逐股API、Quant零LLM。 | 不自动调权；正式默认仍为 RAW 且涨跌停风险子因子关闭。 |
| 复权技术因子与涨跌停风险 | `EXPERIMENTAL` | RAW/QFQ/HFQ 时点方案、涨跌停状态和全A A/B 已完成。 | 尾部排名漂移较大；需公司行为金样本后再决定是否进入生产候选。 |
| 常规午盘推荐 | `CONDITIONAL` | 手工入口、11:32门禁、Top100/分级拉取、结果复用和下午复核已实现。 | 数据库最近运行仍为 `WAITING_AFTERNOON_RECHECK`，尚不能称为每日稳定完成。 |
| iFinD 全A午盘雷达 | `BLOCKED` | 全A覆盖、配额审计和专项工作簿已实现。 | 最近运行是 `FULL_A_QUOTA_SAFETY_FAILED`；专项全量调用消耗超过单次安全阈值，不能作为日常入口。 |
| Midday V2.2 | `SHADOW` | 午盘V2.2一次性运行、下午复核和不可变结果表已实现。 | 最近运行出现 `EMPTY_POOL`；不替代常规午盘生产路径。 |
| 正式盘后全A | `CONDITIONAL` | 数据门禁、Quant、Flash/Pro、正式工作簿、近7日对比、复盘和审计链均已实现。 | `postclose_official_run` 最近记录为 2026-07-20 `PARTIAL_SUCCESS`；需连续成功运行证明稳定性。 |
| LLM Gateway / DeepSeek | `CONDITIONAL` | DeepSeek 接入、Prompt版本、Schema、缓存、预算和审计均存在，数据库有成功调用记录。 | 仓库默认配置仍是 `mock_only=true`、`real_calls_enabled=false`；真实 Flash/Pro 只能由明确确认的正式脚本临时启用，不能无人值守。 |
| Flash V4 / Pro | `CONDITIONAL` | 正式流程、续跑检查点、失败审计和结构化工作簿已实现。 | Prompt和Hash冻结；不得因导出失败重复调用，且不直接决定挂单价或仓位。 |
| Entry Timing V1 | `SHADOW` | 时点评分、Admission V1、数据库、API和报告已实现。 | `enabled=false`、`shadow_only=true`，不创建订单。 |
| Entry Timing / Admission V2.1 | `SHADOW` | 策略分类、市场情绪门禁、策略阈值和历史评价已实现。 | 样本不足，不自动晋级。 |
| Market Regime / Deployment V2.2 | `SHADOW` | 市场状态、部署数量、集中度、入场触发和市场调整评价已实现。 | 当前 2026-07-20 样本为100只 `SHADOW_BLOCK`；保持 Shadow。 |
| Admission V3 | `SHADOW` | 概率策略分类、EV、风险调整机会、四层准入和反事实解释已实现。 | 当前100只均为 `REVIEW`，不得制造 PASS/REJECT 或修改阈值。 |
| Decision Explainability | `SHADOW` | 515条时间合同、六因子归因、Gate解释、只读API和前端页面已实现。 | LLM贡献仍标记 `OPAQUE_LLM_CONTRIBUTION`；不允许宣称已拆到六个底层因子。 |
| Forward Shadow Outcome | `SHADOW` | 515条结果；NEXT_OPEN、滑点、可成交性、D1/D3/D5/D10、Gate价值、因子表现、只读API和13表Excel已实现。 | 截至7月22日为 D1 413、D3 111、D5 44、D10 0；严格公平A/B成熟样本为0，结论为 `KEEP_V22_V3_SHADOW`。 |
| Quant Ranking Forward Effectiveness V1 | `SHADOW` | 已实现原始Quant Top100不可变快照、RAW_CLOSE D1/D3/D5/D10、正向Rank IC、Top20-Bottom20、固定五组、日等权周报、版本隔离、CLI/API和审计产物；Legacy与V2首批快照已冻结。 | activation_date=2026-07-24，2026-07-27为D1；默认不导入更早历史。Excel依赖批准的artifact-tool运行时，缺失时仅输出JSON/CSV并标记PARTIAL_SUCCESS。 |
| 选股收益统计 | `OPERATIONAL` | 不可变选股 Cohort、每日/组合收益、增量刷新、API、前端和Excel已实现。 | 它衡量候选价格表现，不代表真实成交或账户盈亏；缺失收益保持空值。 |
| Gate组合反事实 | `SHADOW` | 12类Gate作用域、绑定/共同阻断、局部价值、Leave-One-Gate-Out和Shapley预留已实现。 | 当前样本和公平比较仍不足，不能据此修改生产门槛。 |
| 大盘次日方向 GRU | `EXPERIMENTAL` | 上证综指日线、严格walk-forward、基线、bootstrap、多种子和报告已完成。 | GRU准确率52.01%，低于多数类基线52.82%，结论 `WEAK_OR_UNSTABLE_EDGE`；停止生产集成。 |
| 市场复盘 | `OPERATIONAL` | 数据快照、证据、驱动、情景和工作簿已实现。 | 最近运行是 `DATA_ONLY`；没有合法LLM时不能伪装成完整AI复盘。 |
| 盘中监控 | `PARTIAL` | 监控池、规则、刷新、告警和操作审计表/API已实现。 | Scheduler关闭，只能手工启动；尚未形成稳定每日运行记录。 |
| 订单价格与仓位建议 | `CONDITIONAL` | 确定性价格计划、止损止盈、仓位预算和验证表已实现。 | 只输出建议；不得由LLM改价、改仓，也不连接券商。 |
| 持仓真相与复核 | `CONDITIONAL` | 人工/AI持仓快照、导入、明确空仓确认和冲突门禁已实现。 | 缺失持仓不能当成空仓；过期或冲突必须阻断建议。 |
| 数据库与审计 | `OPERATIONAL` | SQLite/WAL、版本化模型、输入Hash、运行清单、审计记录和迁移脚本已实现。 | 当前数据库保留91条历史模拟订单和46条历史成交记录；它们不是本阶段新订单，也不代表实盘开启。 |
| 前端工作台 | `OPERATIONAL` | Vue工作台、CenteredDataTable、运行历史、量化、午盘、复盘、绩效、Entry Timing和解释页面已实现。 | 生产包仍需随主线变更重新构建；页面只展示数据库结果。 |
| 内部共享 Web | `DISABLED` | 本地共享密码、审计、Cloudflare部署脚本和发布物已实现。 | `internal_web.enabled=false`；共享账号不能区分四名合伙人的个人身份。 |
| Windows桌面与发布 | `OPERATIONAL` | V1安装包、便携包、PyInstaller后端和Electron构建链已存在。 | 新主线功能尚未声明已重新打包进现有 V1 发布物。 |
| Memory / News / Fundamentals | `PARTIAL` | 数据模型、研究证据、记忆检索、Playbook和相关API已实现。 | 覆盖率和正式日常闭环不一致；不能视为所有股票均已完成研究。 |
| 自动/半自动交易 | `DISABLED` | 仅保留隔离接口、历史模拟模型和安全配置。 | 不在当前路线图内；没有券商连接、自动下单或自动持仓修改授权。 |

## 4. 当前运行证据快照

以下计数用于说明“当前确实实现并运行到哪里”，不是长期KPI：

- Quant Run：11；正式盘后 Run：1；常规午盘 Run：4；全A午盘雷达 Run：8。
- Admission V3 Run：8；Strategy Timing Contract：515；Forward Outcome：515。
- Selection Performance Run：34；Market Review Run：8；iFinD Shadow验收 Run：4。
- Forward Shadow：413个可成交D1、111个D3、44个D5、0个D10；2个因价格超过上限不可成交，另有100个7月22日Shadow等待合法收盘数据。
- 4只 2026-07-20 Baseline正式候选的D1平均收益为 -1.3301%，胜率25%；对应Shadow路线尚未形成相同执行时点的公平成熟样本。
- V3 REVIEW 的当前EV与风险调整机会分层没有表现出可用于晋级的稳定正向区分。
- 代码验证：940 Python tests passed；15 frontend tests passed；frontend typecheck/build、compileall、安全检查通过。

## 5. 当前主线与禁止混用的路线

### 正式主线

```text
Tushare point-in-time batch data
  -> TUSHARE_BASELINE_V1 Quant (RAW, 25/25/20/15/15)
  -> guarded Flash/Pro when explicitly confirmed
  -> deterministic price/position advice
  -> official workbook + review + performance tracking
```

### Shadow主线

```text
Entry Timing V1/V2.1
  -> Market Regime / Admission V2.2
  -> Admission V3 probability + EV
  -> Decision Explainability
  -> Forward Outcome / Gate Value / Factor Performance
```

### 隔离研究

- QFQ/HFQ技术因子和涨跌停风险权重校准。
- 上证综指次日方向GRU实验。
- 任何未来Shapley、自动调参、券商或半自动交易研究。

Shadow和实验结果不得回写正式Quant权重、Flash/Pro Prompt、正式候选、挂单价、仓位或生产配置。

## 6. 后续路线图

### P0：日常运行稳定化

目标：把“功能存在”提升为“连续交易日稳定完成”。

1. 连续记录常规午盘和正式盘后的成功率、数据水位、Provider调用、LLM调用与输出完整性。
2. 解决全A午盘 iFinD 配额安全问题；在额度模型未稳定前继续使用Top100/分级拉取。
3. 统一文档与运行时的LLM语义：仓库默认Mock关闭真实调用，正式脚本只能经显式确认临时开启。
4. 正式盘后必须从 `PARTIAL_SUCCESS` 提升为连续完整成功，且恢复路径不重复消耗LLM。
5. 每日继续生成近7交易日比较，并自动执行Forward Shadow增量回填。

完成标准：至少连续5个交易日入口、数据库状态、工作簿、审计和安全检查一致。

### P1：Forward Shadow有效性验证

目标：回答V2.2/V3和各Gate是否真正改善风险收益。

1. 累积至少20个交易日和100条严格同执行时点的完整D3可成交样本。
2. 分开报告Baseline、V2.2、V3及V3 REVIEW内部排序；禁止混合不同合法入场日。
3. 继续评价Gate避免亏损、错失盈利、重叠归因和组合边际价值。
4. 补齐合法分钟VWAP、行业/指数基准和公司行为调整口径。
5. 在达到 `USABLE` 前不修改生产权重、阈值或Prompt。

当前结论：`KEEP_V22_V3_SHADOW`。

### P1：生产数据质量与价格口径

1. 为QFQ尾部大幅排名变化建立除权除息金样本。
2. 用显式交易日历和停牌台账替代部分K线推断。
3. 决定涨跌停风险子因子和复权技术分是否进入新的生产候选版本；不得直接修改现有Baseline。
4. 建立Tushare/iFinD字段、可用时间和失败降级的持续验收。

### P2：工作台与部署

1. 将Forward Shadow只读面板纳入下一版桌面和内部Web发布物。
2. 若启用内部Web，先完成逐人身份审计方案；共享账号只适合有限内部试用。
3. 增加运行健康页：最近成功午盘/盘后、数据水位、待成熟样本、配额和错误恢复入口。

### P2：研究方向

- 大盘GRU当前停止生产推进；只有在新增信息集和预注册实验方案后才能开启新实验。
- 因子和Gate只做前向评价，不根据单日或小样本自动调参。
- Shapley只在样本和计算成本合理时实现，现有结构保持接口预留。

### Deferred：交易执行

自动下单、半自动交易、真实账户同步和自动持仓修改全部延后，且不因其他阶段完成而自动获得授权。

## 7. 晋级规则

任何Shadow晋级至少需要：

- 时间合同0失败、无未来数据、结果可复现；
- 20个交易日、100条完整D3公平可成交样本；
- Profit Factor不低于1，尾部亏损和False Negative Rate可接受；
- 相对Baseline有稳定区分，不只是在单日或单一市场状态下更好；
- Quant、Flash、Pro Hash和生产配置变更经过独立评审；
- 全量后端、前端、安全、Excel兼容性测试通过；
- 只能提出晋级建议，不允许代码自动修改生产配置。

允许的当前建议为：`KEEP_BASELINE`、`KEEP_V22_V3_SHADOW`、`READY_FOR_V3_CALIBRATION`、`READY_FOR_PRODUCTION_REVIEW`、`BLOCKED`。

## 8. 文档维护规则

- 本文件负责“项目现在做到哪里、下一步做什么”。
- `DAILY_OPERATION_RUNBOOK.md` 负责“每天怎么运行”。
- 各 `ENTRY_TIMING_*`、`IFIND_*`、验收报告负责“某阶段当时做了什么”。
- `archive/v0_3_design/` 只保留历史设计，不再作为当前规划。
- 每完成一个阶段，应更新本文件中的状态、证据日期、限制和下一道门禁；不能只新增阶段报告而不更新总规划。
