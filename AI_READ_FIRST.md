# AI Stock Agent — Read First

本项目是只读决策辅助系统。默认安全边界：

- `ENABLE_REAL_TRADING=false`
- 不创建真实或虚拟订单
- Scheduler 关闭
- Quant、Flash、Pro 和冻结 Decision 按版本隔离
- 历史评估只读取已经落地的结果和本地行情缓存
- 缺失数据保持缺失，不用未来数据、中性分或前收盘价伪造

## 当前模型效度研究

`FULL_UNIVERSE_QUANT_EFFECTIVENESS_V1` 是
`MODEL_STAGE_FORWARD_EFFECTIVENESS_V2` 下的独立 Shadow 扩展。它冻结每日全部合法
Quant 分数和原始排名，按全A十分位、固定500名和前排非等宽区间评估
D1/D3/D5/D10。

该模块不重新计算历史Quant，不调用LLM、搜索或外部Provider，不修改Top100评估，
也不把股票数量当作独立交易日数量。行业映射不满足PIT合同时不计算行业超额；
当前样本不足时结论必须为 `INSUFFICIENT_DATA`。

## 正式盘后 V3.1 Shadow 合同

正式人工入口为 `双击运行_每日工作流程.bat` 的盘后选项，脚本入口为
`scripts/run_v2_postclose_official_once.py`。V2 完成后必须执行
`scripts/run_v3_event_overlay_shadow_once.py --daily-real-search`，不得改回离线占位结果。

- 输入是 `TUSHARE_QUANT_V2_CORRECTED_SHADOW` 全量 Universe 中 Hard Gate 后按排名补足的前100。
- 每股一次结构化最新事件搜索，覆盖全部事件类别；禁止逐 Quant 分项重复搜索、禁止 LLM 重算 Quant。
- 先5只真实Canary，再Top100；同源成功结果幂等复用，失败重跑只补失败股票。
- 新闻/普通事件/Tier 1公告时效分别为36/72/168小时。未来、未知时间、缺URL、过期、重复或非法证据不计分。
- 无合格证据时 V3.1 分必须严格等于 Quant 分；最终 JSON 只允许一次语法修复续轮。
- 真实搜索必须在目标交易日09:25前实际执行。截止后只能复用已冻结成功结果。
- 只有100/100成功、搜索和Provider失败均为0、Hard Gate违规0时才落库和同步Web。
- V3.1保持Shadow；真实订单0、虚拟订单0、Scheduler关闭，不自动晋级生产。

操作和版本合同见 `docs/DAILY_OPERATION_RUNBOOK.md`、`docs/PROJECT_ROADMAP.md`
及 `docs/current/`。
