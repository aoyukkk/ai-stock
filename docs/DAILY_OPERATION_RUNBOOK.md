# 每日常态化运行说明

本文档是项目每日运行的唯一操作基准。所有命令固定使用 Conda 环境 `ai-stock-agent`；系统只生成分析、价格计划和仓位建议，不连接券商、不创建真实或虚拟订单。

## 每日双击时间表

项目不创建 Windows 定时任务，也不会自动启动。每天双击根目录的：

```text
双击运行_每日工作流程.bat
```

| 建议时间 | BAT 选项 | 执行内容 | 主要产物 |
|---|---:|---|---|
| 交易日 11:30 左右 | `1` | 常规午盘推荐 | 午盘工作簿和午盘审计 |
| 交易日 17:00 以后 | `2` | 正式盘后全 A | 正式日线、近 7 交易日对比、市场复盘、候选复盘和审计 |

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

不要因导出或格式问题重跑 Flash/Pro。修复后应从 `market`、`export` 或 `monitor` 阶段续跑。

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

## 目录保留与清理规则

- 必须保留：`data/`、`outputs/`、`backups/`、`release/`、`.env` 和配置文件。
- 可直接重建：`build/`、`.pytest_cache/`、`__pycache__/`、`.pyc`、`tmp/`。
- 过时但仍有审计价值的说明移动到 `docs/archive/`，不再放在根目录与当前说明混用。
- 根目录用户入口为 `双击运行_每日工作流程.bat`；`run_midday_once.cmd`、`run_close_once.cmd` 是 BAT 调用的稳定底层入口。日期写死的一次性命令移入备份归档。
