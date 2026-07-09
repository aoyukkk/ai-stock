# AI Trader Assistant

AI Trader Assistant 是面向 A 股短线交易场景的 AI 智能交易辅助系统。V0.3 第一阶段的定位是：AI 提供交易辅助分析、挂单计划建议、风险提醒、虚拟盘验证和每日复盘，交易员人工负责最终实盘交易决策。

## Safety Boundary

- 第一阶段禁止 AI 自动实盘下单。
- `ENABLE_REAL_TRADING` 默认必须为 `false`。
- AI 只做辅助分析、风险提醒、挂单计划解释与虚拟盘模拟。
- 人工负责最终实盘交易决策，不绕过人工确认。
- 挂单价必须由规则模型和行情数据计算，LLM 只能解释、审核和风控。
- 所有外部数据源必须通过 Adapter / Provider。
- 所有 LLM 调用必须经过 LLM Gateway。
- 所有 AI 输出必须可追溯、可复盘、可审计。

## Configuration Priority

运行参数遵守以下优先级：

1. Web UI Runtime Config
2. Database Config
3. Config File YAML
4. Code Default

Phase 0 只提供配置骨架和测试骨架，后续阶段会实现 ConfigService、数据库配置覆盖、前端控制台修改和 `config_history` 审计。

## Phase 0 Scope

当前阶段只初始化项目基础结构：

- 项目目录骨架
- `.env.example`
- `pyproject.toml`
- `requirements.txt`
- 核心 `config/*.yaml` 配置骨架
- pytest 验收测试
- 文档占位目录

当前尚未实现后端 API、数据库模型、量化引擎、LLM Gateway、Agent、挂单价计算、虚拟盘或前端。

## Development

Install dependencies:

```bash
pip install -r requirements.txt
```

Run tests:

```bash
python -m pytest
```

Optional syntax check:

```bash
python -m compileall .
```

## Phase 15 Local Deployment Preparation

Phase 15 adds local startup, environment checks, security checks, and packaging skeletons.

Quick local setup:

```bash
python scripts/init_local_env.py
python scripts/check_environment.py
python scripts/check_security_config.py
python scripts/dev_start_all.py
```

Packaging preparation:

```bash
python scripts/package_backend_pyinstaller.py
python scripts/package_windows_app.py
```

The packaged and local versions remain Mock Provider + Mock LLM + AI Simulation only. `ENABLE_REAL_TRADING` and `real_trading_enabled` must remain `false`; the first V0.3 release is an advisory system and does not perform automatic real-market order placement.

See `deployment/WINDOWS_LOCAL_DEPLOYMENT.md`, `deployment/PACKAGING_GUIDE.md`, and `deployment/SECURITY_CHECKLIST.md`.

## V0.3 Mock-only Freeze

Phase 16 freezes the V0.3 Mock-only advisory release. Run the final acceptance checks with:

```bash
python scripts/run_final_smoke.py
python scripts/run_all_checks.py
```

Final documentation:

- `docs/API_ENDPOINTS_OVERVIEW.md`
- `docs/OPERATOR_QUICK_START.md`
- `docs/FINAL_INTEGRATION_CHECKLIST.md`
- `docs/V0_3_MOCK_ONLY_RELEASE_NOTES.md`
- `docs/NEXT_PHASE_ROADMAP.md`

Next phases may introduce real data providers and real model providers only after human review. V0.3 does not implement real broker access or automatic real-market order placement.
