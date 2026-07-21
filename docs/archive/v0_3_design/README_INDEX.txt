AI Trader Assistant 文档索引
Version: V0.3 memory + config + order-plan enhanced edition
============================================================

一、V0.3 目标
------------------------------------------------------------
本版本用于指导 Codex / 工程团队逐模块开发 A股短线智能体交易辅助系统。
V0.3 在 V0.2 基础上合并了另一份 V1.2 方案中的优秀工程设计：

1. 配置优先级：Web UI > Database > Config File > Default
2. 更细的盘中分层监控：Hot Pool、持仓、挂单、异常事件分别处理
3. 挂单价参数化：ATR、VWAP、支撑阻力、风险收益比、撤单/改价条件
4. 可复盘数据库：prediction_record、decision_snapshot、order_plan、order_price_candidate、order_plan_evaluation
5. 虚拟盘规则增强：T+1、涨跌停、100股、手续费、印花税、滑点、部分成交、挂单撤单改价
6. Codex 阶段开发规则：每阶段只改相关文件，必须测试、报告、提交
7. 前端控制台增强：所有运行模式、阈值、模型、刷新频率、Q/L/N/R/W 都可在前端修改

二、推荐阅读顺序
------------------------------------------------------------
1. PROJECT_SPECIFICATION.txt
   先看项目总规格、业务边界、核心流程、Q/L/N/R/W、AI与人工分工。

2. SYSTEM_CONFIG_DESIGN.txt
   看全部可配置参数、配置优先级、前端需要暴露的按钮与开关。

3. ARCHITECTURE_AND_DATAFLOW.txt
   看盘后、盘前、盘中、新闻、事件、记忆、虚拟盘的数据流。

4. ORDER_PRICE_AND_VIRTUAL_TRADING_SPEC.txt
   看挂单价公式、撤单/改价条件、虚拟盘成交模拟和A股规则。

5. MEMORY_SYSTEM_DESIGN.txt
   看 MemoryOS + A-MEM + Reflexion + Temporal KG 的工程落地方式。

6. DATABASE_AND_API_CONTRACTS.txt
   看数据库表、索引、API接口契约。

7. PROMPT_TEMPLATES_AND_LLM_GATEWAY.txt
   看 LLM Gateway、模型路由、Prompt模板、JSON输出约束、成本控制。

8. FRONTEND_CONTROL_PANEL_SPEC.txt
   看 Vue3 + Element Plus + Electron 的交易员工作台和配置控制台。

9. CODEX_DEVELOPMENT_PROMPTS.txt
   直接给 Codex 分阶段开发使用。

10. CONFIGURATION_AND_ENV_TEMPLATES.txt
    直接复制到 config/ 和 .env.example 的初始配置模板。

11. TESTING_AND_ACCEPTANCE_CHECKLIST.txt
    每阶段验收和自测清单。

12. MIGRATION_NOTES_V0_3.txt
    V0.3 相比 V0.2 的变更说明。

三、给 Codex 的使用方式
------------------------------------------------------------
建议一次只喂给 Codex 以下文件：

阶段0-2：
- PROJECT_SPECIFICATION.txt
- SYSTEM_CONFIG_DESIGN.txt
- DATABASE_AND_API_CONTRACTS.txt
- CODEX_DEVELOPMENT_PROMPTS.txt

阶段3-8：
- ARCHITECTURE_AND_DATAFLOW.txt
- ORDER_PRICE_AND_VIRTUAL_TRADING_SPEC.txt
- CONFIGURATION_AND_ENV_TEMPLATES.txt
- TESTING_AND_ACCEPTANCE_CHECKLIST.txt

阶段9以后：
- MEMORY_SYSTEM_DESIGN.txt
- PROMPT_TEMPLATES_AND_LLM_GATEWAY.txt
- FRONTEND_CONTROL_PANEL_SPEC.txt

四、强制原则
------------------------------------------------------------
1. 第一版不自动实盘下单，只做人工实盘参考 + AI虚拟盘。
2. 所有外部数据源必须走 Provider / Adapter。
3. 所有 LLM 调用必须走 LLM Gateway。
4. 所有交易建议必须保存 prediction_record 和 decision_snapshot。
5. 所有挂单价必须由规则/量化模块计算，LLM 只做解释、审核、风险判断。
6. 所有配置必须可在前端控制台查看，关键参数可修改，修改写入 config_history。
7. 不允许硬编码 Q/L/N/R/W、模型名、刷新频率、因子权重、风控阈值。
8. 每个 Codex 阶段必须有测试、日志、开发报告。

END
