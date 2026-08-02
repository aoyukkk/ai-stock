# 项目目录盘点与清理基准

> 2026-07-30 治理覆盖：当前阶段只建立规则和迁移清单，不删除、移动或清理任何历史文件。最新分类以 [产物与保留策略](ARTIFACT_AND_RETENTION_POLICY.md) 和 `scripts/SCRIPT_CLASSIFICATION.yaml` 为准；下文 2026-07-20 数字仅为历史盘点快照，不应作为新的清理授权。

盘点日期：2026-07-20。盘点时项目约 49,098 个文件、6.97 GB。

清理完成后约 44,689 个文件、5.39 GB，减少约 4,409 个文件和 1.46 GB。测试发现 `build/pyinstaller/ai_trader_backend.spec` 属于发布契约而非普通构建垃圾，已单独恢复；除该文件外没有恢复构建中间产物。

## 目录分类

| 目录 | 盘点规模 | 处理原则 |
|---|---:|---|
| `data/` | 约 4.28 GB | 保留。包含 3.67 GB Provider 缓存、523 MB 运行报告和 83.82 MB 数据库；每日流程依赖，不能按“缓存”名称误删。 |
| `build/` | 约 1.36 GB | 删除生成内容；仅保留桌面发布契约测试依赖的 `build/pyinstaller/ai_trader_backend.spec`。其余 PyInstaller/Electron/seed 中间产物可重建。 |
| `frontend/` | 约 498 MB | 保留。含前端源码和当前测试/构建依赖。 |
| `release/` | 约 455 MB | 保留。包含可交付安装包、便携包和内部 Web 发布物。 |
| `outputs/` | 约 259 MB | 保留。正式结果、历史工作簿、JSON/Markdown 和审计链。 |
| `tmp/` | 约 73 MB | 删除。渲染预览、检查输出和会话级构建支持文件。最终工作簿已复制到 `outputs/`。 |
| `backups/` | 约 27 MB | 保留。数据库和一次性命令的可恢复归档。 |
| `logs/` | 约 4 MB | 保留既有运行和故障日志；不创建自动任务专用日志。 |

## 直接删除项

- `build/` 中除 `pyinstaller/ai_trader_backend.spec` 外的生成内容
- `.pytest_cache/`
- 全项目 `__pycache__/` 和 `.pyc`
- `tmp/`

这些文件均可从源码、依赖或正式输出重建，不参与业务审计。

## 归档项

- 根目录 V0.3 `.txt` 设计资料移动至 `docs/archive/v0_3_design/`。
- 根目录日期写死的 2026-07-20 午盘/盘后 `.cmd` 移至 `backups/one_shot_commands_20260720/`。
- 相应 Python 实现保留在 `scripts/`，因为测试和历史运行审计仍会引用它们。

## 永久保留项

- `.env`、`.env.example`、`config/` 和数据库迁移。
- `data/ai_trader_dev.db`、Tushare/iFinD 缓存及 `data/reports/`。
- `outputs/` 下全部历史日期和 2026-07-20 正式产物。
- `backups/pre_ifind_20260714/`。
- `release/` 下现有安装包和内部 Web 发布物。
- 工作区中与本次任务无关的已修改/未跟踪源代码。

## 根目录稳定入口

- `双击运行_每日工作流程.bat`：用户每天双击的唯一菜单入口。
- `run_midday_once.cmd`
- `run_close_once.cmd`
- `run_v3_event_overlay_shadow_once.cmd`：V3.1 日常 Shadow 的独立恢复入口；正常盘后已由 `run_close_once.cmd` 自动调用，无需再次双击。

项目不配置 Windows 定时任务；上述 `.cmd` 仅作为 BAT 菜单调用或故障恢复的底层兼容入口。正式盘后编排脚本为 `scripts/run_v2_postclose_official_once.py`，V3.1 子阶段为 `scripts/run_v3_event_overlay_shadow_once.py`。其余一次性脚本不应重新放回根目录。

## 2026-07-30 非破坏性治理清单

- `tmp/` 与 `temp/` 已统一纳入忽略和保留策略，但未删除现有内容。
- `build/` 保留 PyInstaller spec 等发布契约；其余可重建物只能在单独批准后处理。
- `release/` 的说明/manifest 与二进制发布物分开治理；本阶段临时打包只写入任务专属输出目录。
- `reports/` 保留正式审查报告，自动生成报告使用明确的 `generated/` 子目录或任务专属输出目录。
- `backups/` 不进入 Git，任何权限拒绝都使布局审计状态为不完整。
- 已跟踪脚本逐项列入 `scripts/SCRIPT_CLASSIFICATION.yaml`，不移动、不删除。
- `scripts/audit_workspace_layout.py` 是只读盘点入口，不具备删除功能。
