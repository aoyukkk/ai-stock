# 产物与保留策略

状态：`POLICY_READY`
生效范围：本地工作区与桌面临时构建
原则：本策略不授权自动删除、移动、归档、Git GC 或历史重写。

任何清理动作都必须先完成所有权确认、正在运行进程检查、可恢复备份验证、敏感性检查和必要 Hash 记录。业务产物、数据库、Provider 缓存与“可重建文件”不是同一类别。

| 类别 | 路径示例 | 进入 Git | 默认保留 | 可删除条件 | 删除前检查与备份 | 敏感性 | Hash 要求 |
|---|---|---|---|---|---|---|---|
| 源码 | `backend/`、`frontend/src/`、`scripts/` | 是 | 永久 | 仅经代码评审 | 引用、测试、历史兼容 | 中 | 发布提交 Hash |
| 版本化配置 | `config/`、`.env.example` | 是 | 永久 | 仅经配置评审 | 默认值、安全门禁 | 中 | 发布提交 Hash |
| 本地 Secret 配置 | `.env`、用户级 safeStorage | 否 | 轮换后按流程处理 | 仅用户显式授权 | 迁移验证、回滚路径、进程关闭 | 极高 | 禁止记录 Secret Hash |
| 运行状态 | `data/*.db`、`logs/` | 否 | 数据库按业务要求；日志默认 90 天 | 数据所有者批准且服务停止 | 一致性检查、可恢复备份 | 高 | 数据库备份要求 SHA-256 |
| Provider 缓存 | `data/cache/` | 否 | 至少覆盖回溯和审计窗口 | 可证明可重取且不破坏 PIT 审计 | Provider 配额、时间血缘、离线需求 | 高 | 正式运行引用缓存需 Hash |
| 不可变业务产物 | `outputs/YYYY-MM-DD/` | 依现行精确 allowlist | 永久或业务审计期限 | 默认不可删除 | 与数据库、审计链、工作簿对应验证 | 高 | 必须 SHA-256 |
| 可重建构建物 | `frontend/dist/`、`dist-electron/`、`build/` 中间物 | 否 | 最近一次本地验收即可 | 从锁定源码可重建 | 确认不含唯一 spec/配置 | 中 | 临时 manifest 记录 |
| 发布包 | `release/` 二进制、临时 `desktop_build/` | 二进制否；说明可版本化 | 已批准版本按发布政策 | 替代版本已验收且有备份 | 敏感扫描、签名、安装烟测 | 高 | 必须 SHA-256 |
| 备份 | `backups/` | 否 | 按备份类型，至少保留一个已验证恢复点 | 明确恢复点替代并通过恢复演练 | ACL、完整性、数据库句柄关闭 | 极高 | 必须 SHA-256 |
| 临时文件 | `tmp/`、`temp/` | 否 | 会话级 | 进程关闭且结果已复制 | 确认无唯一审计证据或 Secret | 中至高 | 通常不要求 |
| 模型权重 | 本地模型目录、外部缓存 | 否，除非另有 LFS 方案 | 与模型版本一致 | 可验证来源可恢复 | 许可证、来源、兼容性 | 中 | 必须记录供应方 Hash |
| 审计报告 | `reports/` 版本化报告、`outputs/**/审计/` | 正式审查报告是；生成中间物否 | 正式报告永久 | 经治理评审 | 脱敏、证据路径、报告状态 | 高 | 正式报告建议 SHA-256 |

## 路径治理

- `tmp/` 与 `temp/` 都是会话级临时目录，统一忽略；本策略不删除现有内容。
- `build/pyinstaller/ai_trader_backend.spec` 是发布契约，不属于普通构建垃圾。
- `release/` 中二进制包与版本化发布说明分开治理；本阶段不触碰现有包。
- `reports/` 根目录保留经评审、可版本化的 Markdown 报告；自动生成中间报告写入 `reports/generated/` 或任务专属 `outputs/`。
- `outputs/security_remediation_20260730/desktop_build/` 仅用于本阶段隔离验收，不复制到正式 `release/`。
- `backups/` 即使可见也不得由治理脚本删除；权限拒绝必须报告为审计不完整。

## 脚本与入口

唯一推荐用户入口为根目录 `双击运行_每日工作流程.bat`；其兼容子入口为 `run_midday_once.cmd` 和 `run_close_once.cmd`。正式盘后脚本入口为 `scripts/run_v2_postclose_official_once.py`，并默认追加 V3.1 Shadow；`scripts/run_v3_event_overlay_shadow_once.py` 仅作为该阶段的恢复入口。其他脚本的用途和风险见 `scripts/SCRIPT_CLASSIFICATION.yaml`。分类不等于执行授权。

只读盘点使用：

```powershell
conda run -n ai-stock-agent python scripts/audit_workspace_layout.py
```

它只生成目录大小、文件数、未跟踪大文件、敏感路径候选、日期写死脚本、可重建目录、权限拒绝和建议动作，不执行清理。
