# AI Trader Assistant

AI Trader Assistant 是面向 A 股的只读决策辅助系统，覆盖全 A 量化、午盘分析、Flash/Pro 复核、规则价格计划、市场复盘和候选绩效跟踪。交易员始终负责最终实盘决策。

## 安全边界

- `.env` 必须保持 `ENABLE_REAL_TRADING=false`。
- 项目内部 `Scheduler=false`，未配置 Windows 定时任务；所有流程由用户双击 BAT 后手工启动。
- 全 A Quant 不调用 LLM；Flash/Pro 必须经过 LLM Gateway。
- 挂单价和仓位建议由确定性规则生成，LLM 不能改价、改仓或绕过风险门禁。
- 不自动下单、不修改持仓；外部行情调用和工作簿输出均保留审计。

## 每日入口

每天双击项目根目录的 `双击运行_每日工作流程.bat`，再选择：

- `1`：11:30 左右运行常规午盘推荐，程序会等待到 11:32 门禁。
- `2`：17:00 后运行正式盘后全 A 流程，包括 Tushare 时点门禁、Quant、Flash、Pro、正式工作簿、滚动近 7 交易日对比和复盘表。

程序不会自动定时启动。重复运行默认复用当天已经成功的结果，不重复消耗 Provider 或 LLM。完整运行和失败恢复说明见 [每日常态化运行说明](docs/DAILY_OPERATION_RUNBOOK.md)。

## 常用命令

所有 Python 命令固定使用 Conda 环境：

```powershell
conda run -n ai-stock-agent python -m pytest -q
conda run -n ai-stock-agent python -m compileall . -q
conda run -n ai-stock-agent python scripts/check_security_config.py
```

双击入口或在终端执行：

```powershell
双击运行_每日工作流程.bat
```

## 主要目录

- `backend/`、`frontend/`：工作台和应用服务。
- `datasource/`、`midday/`、`post_close/`、`review/`：行情、午盘、盘后和绩效业务模块。
- `config/`：版本化配置；运行时优先级为 Web UI > Database > YAML > Code Default。
- `data/`：数据库和 Provider 缓存，不属于临时文件。
- `outputs/YYYY-MM-DD/`：正式工作簿、JSON、Markdown 和审计产物。
- `logs/`：运行日志和历史故障记录。
- `docs/`：当前说明；旧 V0.3 设计资料归档在 `docs/archive/v0_3_design/`。
- `build/`、`tmp/`、`__pycache__/`：可重建内容，可按清理说明删除。

## 内部共享工作台

四名项目合伙人可通过 Cloudflare Tunnel 共用内部工作台。源站只监听 `127.0.0.1:8080`，密码不写入仓库或环境文件。部署步骤见 [Cloudflare Tunnel 共享账号部署](docs/CLOUDFLARE_INTERNAL_DEPLOYMENT.md)。共享账号只能审计到共享主体，无法区分具体合伙人；真实交易仍保持关闭。

## 进一步阅读

- [文档索引](docs/README.md)
- [输出目录规范](docs/OUTPUT_LAYOUT.md)
- [故障排查](docs/TROUBLESHOOTING_V1.md)
- [本地部署](deployment/WINDOWS_LOCAL_DEPLOYMENT.md)
- [安全检查](deployment/SECURITY_CHECKLIST.md)
