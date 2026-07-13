# 交易员日度筛选工作台 V1

## 产品定位

工作台面向单一交易员的日度筛选流程：检查本地数据、查看全A Quant、查看 Flash V4 二筛、维护人工候选池、查看已有终排/挂单/仓位/基本面，并登记已有导出结果。它不提供实盘下单、虚拟订单、自动调度、实时监控或旧版复杂控制台入口。

流程为：数据检查 -> Quant -> Flash -> 人工选股 -> Pro 终排 -> Excel。当前前端默认使用数据库中已经完成的结果，或明确的 Mock 记录；不会因为点击页面按钮访问外部数据源或调用真实 LLM。

## 页面与路由

| 路由 | 页面 |
| --- | --- |
| `/workbench` | 流程工作台与步骤门禁 |
| `/data-status` | 本地数据与 Temporal Gate 状态 |
| `/quant-ranking` | 服务端分页的全A Quant 排名 |
| `/llm-screening` | Flash V4 二筛结果 |
| `/manual-selection` | 持久化人工候选池 |
| `/final-ranking` | 终排结果 |
| `/order-position` | 规则挂单价与仓位结果 |
| `/fundamentals` | 重点基本面结果 |
| `/runs` | 持久化工作台任务记录 |
| `/settings` | 筛选数量与本地后端密钥状态 |

## 门禁与任务

- 数据检查未通过 Temporal Gate 时，Quant 按钮禁用。
- Quant 未完成时，Flash 按钮禁用。
- Flash 未完成且人工池为空时，终排按钮禁用。
- 终排未完成时，导出按钮禁用。
- `pipeline_job` 持久化 `job_id`、交易日、状态、进度、计数、Token、成本、Run ID 与 checkpoint。
- 同交易日同类型运行中的任务会被阻止；相同已完成的本地结果会复用已有任务记录。
- 取消仅影响运行中任务；失败或取消的任务可通过 `POST /api/workbench/jobs/{job_id}/resume` 建立带 `resumed_from` checkpoint 的本地恢复记录。

本阶段工作台只允许 `USE_EXISTING` 与 `MOCK` 模式。`MISSING_ONLY` 与 `FORCE_REFRESH` 数据更新请求被明确拒绝，不会隐式请求 Tushare；真实 LLM 与真实数据执行必须在后续明确启用的应用服务阶段完成。

## 设置与密钥

筛选数量、Token 预警、Flash 并发/批次和验证账户参数通过 `SystemConfig` 持久化；每次修改写入 `ConfigHistory`，但密钥从不写入该表。

密钥只在本地后端进程环境中暂存：

- Renderer 只提交输入值，随后立即清空输入框。
- Pinia、LocalStorage、SessionStorage、数据库、日志、Excel 和 `ConfigHistory` 都不保存密钥。
- `GET /api/workbench/secrets/status` 只返回是否已配置与测试状态。
- 设置、测试和删除接口都不返回原始密钥。

## 居中表格规范

`frontend/src/components/common/CenteredDataTable.vue` 是工作台全部结果表的唯一表格封装。它对列统一使用 `align="center"` 与 `header-align="center"`。全局样式同时保证表头、单元格、按钮、复选框与长文本水平/垂直居中，长文本自动换行，股票代码按字符串渲染以保留前导零。

Excel 生成器共同复用 `scripts/excel_alignment.mjs` 中的 `applyCenteredAlignment`。Trader Demo、日度人工可读工作簿和验证工作簿的表头/数据区域均使用：水平居中、垂直居中、自动换行；股票代码列继续保留文本格式。

## 本地启动与验证

后端使用 Conda 环境：

```powershell
conda run -n ai-stock-agent python -m pytest -q
conda run -n ai-stock-agent python scripts/check_security_config.py
conda run -n ai-stock-agent python -m compileall -q .
```

前端：

```powershell
cd frontend
npm run test
npm run typecheck
npm run build
```

Electron 打包不属于 V1 完成范围；先验证 Web 前端和本地 FastAPI 的行为。
