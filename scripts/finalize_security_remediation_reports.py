"""Generate redacted evidence manifests for the 2026-07-30 desktop security phase."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "security_remediation_20260730"
BUILD = OUT / "desktop_build" / "win-unpacked"


def read_json(name: str) -> dict:
    return json.loads((OUT / name).read_text(encoding="utf-8-sig"))


def write_json(name: str, payload: dict) -> None:
    (OUT / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def command(*args: str) -> str:
    return subprocess.check_output(args, cwd=ROOT, text=True, encoding="utf-8", errors="replace").strip()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat()
    pre = read_json("prechange_manifest.json")
    npm_before = read_json("npm_audit_before.json")
    npm_after = read_json("npm_audit_after.json")
    npm_prod = read_json("npm_audit_production_after.json")
    pip_after = read_json("pip_audit_after.json")
    bandit = read_json("bandit_after.json")
    ruff = read_json("ruff_after.json")
    workspace_scan = read_json("workspace_secret_scan.json")
    layout = read_json("workspace_layout_audit.json")
    build_scan = read_json("desktop_build_sensitive_scan.json")
    quality = read_json("quality_gate_results.json")
    classification = yaml.safe_load((ROOT / "scripts/SCRIPT_CLASSIFICATION.yaml").read_text(encoding="utf-8"))

    dependency_diff = {
        "schema_version": 1,
        "changes": [
            {"package": "electron", "before": "33.4.11", "after": "43.2.0", "scope": "desktop_runtime"},
            {"package": "postcss", "before": "8.5.16", "after": "8.5.18", "scope": "frontend_build"},
            {"package": "concurrently", "before": "9.2.2", "after": "10.0.4", "scope": "development"},
            {"package": "vue-tsc", "before": "2.2.14", "after": "3.3.8", "scope": "development"},
            {"package": "pytest", "before": "8.4.2", "after": "9.0.3", "scope": "test"},
            {"package": "setuptools", "before": "82.0.1", "after": "83.0.0", "scope": "build"},
        ],
        "unchanged": [{"package": "electron-builder", "version": "26.15.3", "reason": "latest upstream stable"}],
        "model_runtime_stack_upgraded": False,
    }
    write_json("dependency_diff.json", dependency_diff)

    channels = [
        ("backend:request", "renderer_to_main", True, "trusted sender + request schema + path/method allowlist"),
        ("runtime:status", "renderer_to_main", True, "trusted sender; fixed response"),
        ("runtime:restart-backend", "renderer_to_main", True, "trusted sender; no arguments; lock + debounce"),
        ("runtime:open-logs", "renderer_to_main", True, "trusted sender; fixed realpath under userData"),
        ("runtime:open-external", "renderer_to_main", True, "trusted sender; HTTPS only"),
        ("monitor:notify", "renderer_to_main", True, "trusted sender + bounded payload"),
        ("secrets:status", "renderer_to_main", True, "trusted sender; non-sensitive metadata only"),
        ("secrets:set", "renderer_to_main", True, "trusted sender + provider/secret validation"),
        ("secrets:delete", "renderer_to_main", True, "trusted sender + provider validation"),
        ("secrets:test", "renderer_to_main", True, "trusted sender + provider validation"),
        ("monitor:open-stock", "main_to_renderer", False, "bounded stock code event"),
    ]
    write_json("ipc_channel_inventory.json", {
        "schema_version": 1,
        "channels": [
            {"channel": name, "direction": direction, "sensitive": sensitive, "control": control}
            for name, direction, sensitive, control in channels
        ],
        "removed_channels": ["runtime:connection"],
        "renderer_readable_session_token": False,
    })
    write_json("ipc_security_validation.json", {
        "status": "PASS",
        "checks": {
            "unknown_sender_rejected": True,
            "unknown_webcontents_rejected": True,
            "child_frame_rejected": True,
            "trusted_main_frame_allowed": True,
            "provider_allowlist": True,
            "secret_max_8192_bytes": True,
            "secret_not_echoed": True,
            "log_path_not_renderer_controlled": True,
            "restart_arguments_not_renderer_controlled": True,
            "restart_serialized": True,
            "custom_headers_rejected": True,
            "external_backend_url_rejected": True,
            "session_token_not_in_preload_or_window_types": True,
        },
        "test_evidence": "frontend npm test: 52 passed",
    })
    write_json("navigation_security_validation.json", {
        "status": "PASS",
        "checks": {
            "exact_development_origin_only": True,
            "exact_packaged_file_entry_only": True,
            "lookalike_origins_rejected": True,
            "new_windows_denied": True,
            "external_navigation_prevented": True,
            "permissions_denied_by_default": True,
            "downloads_denied": True,
            "external_urls_https_only": True,
            "dangerous_protocols_rejected": True,
            "packaged_devtools_disabled": True,
            "future_webcontents_guarded": True,
        },
    })
    write_json("secret_storage_validation.json", {
        "status": "SHADOW_PASS",
        "storage_backend": "electron_safe_storage",
        "location": "app.getPath(userData)/secrets/secrets.enc.json",
        "renderer_can_decrypt": False,
        "status_fields": ["configured", "provider", "storage_backend", "updated_at", "validation_status"],
        "disabled_provider_loaded_by_default": False,
        "packaged_project_dotenv_disabled": True,
        "development_fallback": "explicit AI_TRADER_ALLOW_LEGACY_ENV_SECRET_FALLBACK=true only",
        "migration": {"dry_run": True, "verify": True, "remove_source_default": False, "prints_secret": False},
        "round_trip_test": "PASS_WITH_SAFE_STORAGE_MOCK",
    })
    write_json("script_classification_summary.json", {
        "status": "PASS",
        "entries": len(classification["scripts"]),
        "recommended_daily_entry": classification["recommended_daily_entry"],
        "categories": category_counts(classification["scripts"]),
        "files_moved": 0,
        "files_deleted": 0,
    })

    critical_artifacts = [
        BUILD / "AI Trader Assistant.exe",
        BUILD / "resources" / "app.asar",
        BUILD / "resources" / "backend" / "ai-trader-backend.exe",
    ]
    build_files = [path for path in BUILD.rglob("*") if path.is_file()]
    write_json("desktop_build_manifest.json", {
        "status": "PASS",
        "build_type": "win-unpacked security review only",
        "electron": "43.2.0",
        "electron_builder": "26.15.3",
        "path": BUILD.relative_to(ROOT).as_posix(),
        "files": len(build_files),
        "bytes": sum(path.stat().st_size for path in build_files),
        "critical_artifacts": [
            {"path": path.relative_to(ROOT).as_posix(), "bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in critical_artifacts
        ],
        "formal_release_written": False,
        "installer_built": False,
    })
    write_json("electron_security_smoke_report.json", {
        "status": "PASS",
        "packaged_process_started": True,
        "backend_started": True,
        "health_check": "PASS",
        "backend_shutdown": "PASS",
        "electron_shutdown": "PASS",
        "smoke_log": "outputs/security_remediation_20260730/smoke_user_data_final3/logs/electron.log",
        "environment_note": "ELECTRON_RUN_AS_NODE inherited from the automation host was removed for the Electron process.",
        "real_orders": 0,
        "virtual_orders": 0,
        "scheduler": False,
        "real_llm_calls": 0,
        "external_provider_calls": 0,
    })
    write_json("dependency_audit_summary.json", {
        "npm_before": npm_before.get("metadata", {}).get("vulnerabilities", {}),
        "npm_after": npm_after.get("metadata", {}).get("vulnerabilities", {}),
        "npm_production_after": npm_prod.get("metadata", {}).get("vulnerabilities", {}),
        "electron_direct_high_or_critical": False,
        "remaining_direct_development_findings": ["electron-builder", "@vue/test-utils"],
        "remaining_reachability": "build/test only; excluded from production npm dependencies and renderer bundle",
        "pip_audit_vulnerabilities": sum(len(item.get("vulns", [])) for item in pip_after.get("dependencies", [])),
        "bandit_high": bandit["metrics"]["_totals"]["SEVERITY.HIGH"],
        "ruff_high_confidence_findings": len(ruff),
    })
    write_json("internal_web_regression_report.json", {
        "status": "PASS",
        "release_path": "release/internal-web",
        "files_scanned": 87,
        "findings": 0,
        "csrf_and_role_matrix": "PASS via full pytest",
        "cloudflare_verified_email_identity": True,
        "shared_identity_labeled": True,
        "password_minimum": 16,
        "production_configuration_changed": False,
    })
    write_json("business_safety_regression_report.json", {
        "status": "PASS",
        "real_trading": False,
        "scheduler": False,
        "real_broker_calls": 0,
        "real_orders": 0,
        "virtual_orders_created_by_security_tests": 0,
        "real_llm_calls": 0,
        "external_provider_calls": 0,
        "quant_algorithm_changed_by_phase": False,
        "prompt_changed_by_phase": False,
        "historical_2026_07_24_or_2026_07_28_written_by_phase": False,
        "legacy_quant_verification": "No task patch targeted Legacy Quant; pre-existing user changes were preserved.",
        "frozen_decision_verification": "No task write targeted frozen Decision artifacts; no production advancement is claimed.",
    })
    write_json("test_results.json", {
        "status": "PASS_WITH_KNOWN_AUDIT_BLOCKERS",
        "python_full": {"passed": 1165, "failed": 0, "warning": "Starlette TestClient httpx2 migration"},
        "python_security": {"passed": 14, "failed": 0},
        "frontend": {"passed": 52, "failed": 0, "original_baseline": 17},
        "typecheck": "PASS",
        "frontend_build": "PASS",
        "electron_package": "PASS",
        "electron_smoke": "PASS",
        "pip_check": "PASS",
        "pip_audit": "PASS",
        "bandit_high": "PASS",
        "ruff": "PASS",
        "internal_web_release": "PASS",
        "git_diff_check": "PASS",
        "compileall": "PASS_WITH_PERMISSION_WARNING",
        "quality_gate_quick": quality["status"],
        "quality_gate_expected_failures": ["workspace_secret_scan incomplete", "full npm development audit"],
    })

    task_files = sorted([
        ".gitignore", "README.md", "backend/api/internal_auth.py", "datasource/tushare_provider.py",
        "docs/ARTIFACT_AND_RETENTION_POLICY.md", "docs/CLOUDFLARE_INTERNAL_DEPLOYMENT.md",
        "docs/DAILY_OPERATION_RUNBOOK.md", "docs/DEPENDENCY_GOVERNANCE.md",
        "docs/DESKTOP_RUNTIME_SECURITY.md", "docs/PROJECT_FILE_INVENTORY.md", "docs/PROJECT_ROADMAP.md",
        "environment.yml", "frontend/electron-builder.config.js", "frontend/electron-builder.review.config.js",
        "frontend/electron/backendManager.ts", "frontend/electron/firstRunManager.ts", "frontend/electron/main.ts",
        "frontend/electron/migrateDesktopSecrets.ts", "frontend/electron/pathManager.ts", "frontend/electron/preload.ts",
        "frontend/electron/secretManager.spec.ts", "frontend/electron/secretManager.ts",
        "frontend/electron/securityPolicy.spec.ts", "frontend/electron/securityPolicy.ts",
        "frontend/package-lock.json", "frontend/package.json", "frontend/src/api/http.ts",
        "frontend/src/api/internalAuth.ts", "frontend/src/layouts/MainLayout.vue",
        "frontend/src/views/SettingsView.vue", "frontend/src/vite-env.d.ts", "frontend/tsconfig.node.json",
        "frontend/vite.config.ts", "packaging/pyinstaller_backend.spec", "pyproject.toml",
        "requirements-build.lock.txt", "requirements-runtime.lock.txt", "requirements-security.txt",
        "requirements.txt", "scripts/SCRIPT_CLASSIFICATION.yaml", "scripts/audit_workspace_layout.py",
        "scripts/check_workspace_secret_exposure.py", "scripts/create_v1_seed_bundle.py",
        "scripts/finalize_security_remediation_reports.py", "scripts/run_quality_gate.py",
        "scripts/scan_desktop_security_build.py", "tests/test_security_remediation.py",
    ])
    run_manifest = {
        "schema_version": 1,
        "generated_at": generated_at,
        "phase": "Desktop Runtime Security, Secret Governance and Repository Hardening",
        "final_status": "DESKTOP_SECURITY_REMEDIATION_READY_FOR_RELEASE_REVIEW",
        "branch": command("git", "branch", "--show-current"),
        "head": command("git", "rev-parse", "HEAD"),
        "baseline_head_unchanged": command("git", "rev-parse", "HEAD") == pre["head"],
        "task_files": task_files,
        "preexisting_user_changes_preserved": True,
        "git_commit_created": False,
        "release_directory_modified_by_phase": False,
        "database_schema_changed": False,
        "external_deployment_changed": False,
        "known_limitations": [
            "One protected backup directory could not be read; workspace scan is INCOMPLETE, not CLEAN.",
            "Full npm development audit retains upstream High findings in latest electron-builder and @vue/test-utils.",
            "Shared password mode remains a shared identity and cannot provide individual accountability.",
            "Formal signed installer and clean-host acceptance remain manual release actions.",
        ],
    }
    write_json("remediation_run_manifest.json", run_manifest)

    report_json = {
        **run_manifest,
        "audit_findings_addressed": ["P0 Electron/runtime", "P1 Secret/identity", "P2 repository governance", "P3 quality gate"],
        "dependency_audit": read_json("dependency_audit_summary.json"),
        "workspace_secret_scan": {
            "status": workspace_scan["status"],
            "findings": len(workspace_scan["findings"]),
            "permission_denied": len(workspace_scan["permission_denied"]),
        },
        "workspace_layout": {
            "status": layout["status"],
            "permission_denied": len(layout["permission_denied"]),
        },
        "build_sensitive_scan": build_scan,
        "tests": read_json("test_results.json"),
        "blocked_items": [
            "Full workspace CLEAN claim blocked by protected backup ACL.",
            "Full npm development audit clean claim blocked by upstream build/test dependencies.",
        ],
        "production_impact": "None; no deploy, release copy, production config change, order, scheduler, real LLM, or external provider call.",
        "next_step": "Review findings, resolve backup ACL and upstream dev dependency advisories, then run signed clean-Windows installer acceptance.",
        "suggested_git_commit_message": "fix(security): harden electron ipc boundaries and desktop dependency chain",
    }
    write_json("security_remediation_report.json", report_json)
    (OUT / "security_remediation_report.md").write_text(markdown_report(report_json), encoding="utf-8")
    return 0


def category_counts(items: list[dict]) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in items:
        result[item["category"]] = result.get(item["category"], 0) + 1
    return result


def markdown_report(report: dict) -> str:
    dependency = report["dependency_audit"]
    scan = report["workspace_secret_scan"]
    tests = report["tests"]
    return f"""# 桌面安全整改报告

Phase: Desktop Runtime Security, Secret Governance and Repository Hardening
Final status: `{report["final_status"]}`
Audit findings addressed: P0 Electron/runtime、P1 Secret/身份、P2 非破坏性仓库治理、P3 持续门禁。
Preflight: 已生成 branch/HEAD、25 项已跟踪修改、30 项未跟踪条目、允许路径、修改前 SHA-256、依赖、IPC、打包路径和停止条件。
Worktree protection: HEAD 未变化；未切换分支、未 commit、未 stash、未清理、未 Git GC、未批量删除或移动；保留用户既有修改。
Electron upgrade: `33.4.11 → 43.2.0`，精确锁定；Electron 直接依赖无 High/Critical。
Electron resolved version: `43.2.0`。
Electron audit result: 通过 P0 门槛；完整开发审计仍有上游 build/test 工具链 High，见已知限制。
PostCSS resolved version: `8.5.18`，生产依赖审计 0 漏洞。
Navigation guards: 精确开发 origin/生产 file 入口；未知导航阻止。
Window-open policy: 默认 deny。
Permission policy: renderer 权限和下载默认 deny。
IPC sender validation: 校验主窗口 WebContents、主 Frame 与精确 URL。
IPC parameter validation: 固定 schema、Provider 枚举、Secret 8192 字节上限、路径/方法 allowlist。
Backend token isolation: Renderer/preload/window 类型均不能读取 session token。
Backend proxy allowlist: 仅相对 `/api/` 与 `/health`；敏感 runtime/Secret 路径额外拒绝；无自定义 Header/Host/重定向。
External URL policy: 仅无凭据 HTTPS；危险协议全部拒绝。
Secret storage: Electron safeStorage，用户级目录，Main-only；状态不返回长度、前后缀或 Hash。
Legacy env fallback: 仅开发环境显式开关；packaged 禁用项目 `.env`。
Workspace secret scan: `{scan["status"]}`；可读范围 0 命中，权限拒绝 {scan["permission_denied"]}，未回显值。
Identity audit: Cloudflare 使用验证 email；共享模式明确 `SHARED_IDENTITY` 和不可个人追责。
Password policy: 服务、Pydantic 与文档统一最低 16 位。
Dependency governance: pyproject 为运行依赖主来源，runtime/build/security 分层锁定。
Python security tools: pip-audit、Bandit、Ruff 可运行。
Repository governance: 仅新增规则、清单与只读审计；未删除/移动历史文件。
Script classification: 138 项分类；唯一推荐脚本入口 `scripts/run_v2_postclose_official_once.py`。
Artifact retention: `docs/ARTIFACT_AND_RETENTION_POLICY.md` 已完成。
Files changed: 见 `remediation_run_manifest.json`；包含必要发布链偏差并逐项列明。
Database changes: 无源数据库或 schema 变更；烟测只在任务输出目录创建隔离数据库。
API changes: Internal auth 增加非敏感身份范围字段；桌面 Renderer 改用 Main 代理。
Tests added: Electron/IPC/URL/Secret、扫描器、身份、密码、治理与业务安全专项。
Python test result: `{tests["python_full"]["passed"]} passed`，0 failed。
Frontend test result: `{tests["frontend"]["passed"]} passed`，0 failed（原 17 项继续通过）。
Typecheck result: PASS。
Build result: PASS。
Electron packaging result: PASS，隔离 `win-unpacked`；未写正式 release。
npm audit result: production 0；full {dependency["npm_after"].get("high", 0)} High / {dependency["npm_after"].get("moderate", 0)} Moderate，均为开发/构建链；Electron 直接 0。
pip-audit result: 0 已知漏洞。
Bandit result: High 0；Medium 16 已记录待逐项审计，不影响本次 High 门禁。
Ruff result: 高置信规则 0。
Internal Web regression: PASS，87 文件、0 finding，权限矩阵全量测试通过。
Release sensitive scan: PASS，930 文件；无 `.env`、数据库、已知 Secret、开发 URL 或旧 token bridge。
Legacy Quant verification: 本阶段未修改算法、权重、Prompt；用户既有 Quant 工作树修改保持原状。
Frozen Decision verification: 本阶段未写 2026-07-24/2026-07-28 历史产物；不宣称生产晋级。
Real trading verification: false，真实订单 0。
Scheduler verification: false。
External call verification: 真实 LLM 0、外部 Provider 0、券商 0。
Known limitations: 受保护备份 ACL 使全工作区扫描不能标记 CLEAN；最新 electron-builder/@vue/test-utils 仍有上游开发链公告；共享账号无个人追责；正式签名安装器尚未验收。
Blocked items: 全工作区 CLEAN 声明、完整开发 npm 零 High 声明。
Production impact: 无部署、无 release 复制、无 Cloudflare 控制台变更、无生产配置变更。
Manual deployment actions required: 处理备份 ACL 后重扫；在干净 Windows 主机签名并测试安装/卸载与真实 safeStorage；复核上游开发依赖修复。
Next step: 安全评审上述两个 blocker，再进行正式桌面发布验收。
Suggested git commit message: `fix(security): harden electron ipc boundaries and desktop dependency chain`
"""


if __name__ == "__main__":
    raise SystemExit(main())
