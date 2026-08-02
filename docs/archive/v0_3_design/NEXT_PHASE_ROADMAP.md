# Next Phase Roadmap

> 历史说明（2026-07-22更新）：本文件是V0.3时期的原始未来规划，不再代表当前项目状态。当前唯一规划基准为 [`../../PROJECT_ROADMAP.md`](../../PROJECT_ROADMAP.md)。下方原始阶段编号保留用于追溯。

This roadmap was planning only in V0.3. Several items have since been implemented partially or in Shadow form; none of the changes below authorizes automated trading.

## Implementation reconciliation as of 2026-07-22

| Legacy phase | Current status | Reconciliation |
|---|---|---|
| Phase 17 Real Data Provider | `PARTIAL / SHADOW` | Tushare已成为正式Baseline数据源；iFinD已完成只读Shadow验收，但全A午盘受配额安全门禁限制。新闻、海外和授权覆盖尚未形成统一正式闭环。 |
| Phase 18 Real LLM Provider | `CONDITIONAL` | DeepSeek Gateway、预算、Prompt版本和审计已实现并有历史成功调用；仓库默认仍是Mock/真实调用关闭，正式调用需显式确认。OpenAI/Qwen/Claude未启用。 |
| Phase 19 Historical Backtest | `PARTIAL` | 选股收益、Forward Shadow、Gate反事实和大盘walk-forward实验已实现；尚无被批准的完整账户级生产回测或自动交易回放。 |
| Phase 20 Production Hardening | `PARTIAL` | 安全检查、备份、日志、迁移、Windows发布、内部认证和恢复脚本已存在；日常流程仍需连续成功验证，内部Web默认关闭。 |
| Phase 21 Semi-Automated Trading | `DISABLED` | 无券商连接、无自动下单、无自动持仓修改；不在当前获授权路线内。 |

## PHASE 17 - Real Data Provider Integration

- iFinD Provider.
- Tushare / AKShare fallback.
- News source integration.
- Overseas data source integration.
- Data authorization and compliance checks.

## PHASE 18 - Real LLM Provider Integration

- DeepSeek.
- OpenAI / GPT.
- Optional Qwen / Claude.
- Cost controls.
- Prompt version governance.

## PHASE 19 - Historical Backtest

- Historical market replay.
- Order-price quality evaluation.
- Virtual trading historical replay.

## PHASE 20 - Production Hardening

- Permission system.
- Log rotation.
- Backup.
- Monitoring.
- Recovery workflows.

## PHASE 21 - Optional Semi-Automated Trading Research

- Research only.
- Requires compliance review.
- Disabled by default.
