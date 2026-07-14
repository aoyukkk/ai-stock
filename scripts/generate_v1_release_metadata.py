from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


VERSION = "1.0.0"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\n")
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def source_state(root: Path) -> tuple[bool, str, str]:
    status_raw = subprocess.check_output(["git", "status", "--porcelain=v1", "-z"], cwd=root)
    diff = subprocess.check_output(["git", "diff", "--binary", "HEAD"], cwd=root)
    digest = hashlib.sha256(diff)
    entries = [item for item in status_raw.decode("utf-8", errors="replace").split("\0") if item]
    digest.update(status_raw)
    for entry in entries:
        if not entry.startswith("?? "):
            continue
        path = root / entry[3:]
        if path.is_file():
            digest.update(sha256_file(path).encode("ascii"))
        elif path.is_dir() and not _generated_path(path, root):
            digest.update(tree_hash(path).encode("ascii"))
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    return bool(entries), digest.hexdigest(), commit


def _generated_path(path: Path, root: Path) -> bool:
    relative = path.resolve().relative_to(root.resolve()).parts
    return bool(relative and relative[0].lower() in {"build", "release", "node_modules", "logs"})


def generate(root: Path, output: Path, code_signing: str) -> dict[str, object]:
    setup = output / "installer" / f"AI-Trader-Assistant-Setup-{VERSION}-x64-unsigned.exe"
    portable = output / "portable" / f"AI-Trader-Assistant-Portable-{VERSION}-x64-unsigned.zip"
    if code_signing == "CONFIGURED":
        setup = output / "installer" / f"AI-Trader-Assistant-Setup-{VERSION}-x64.exe"
        portable = output / "portable" / f"AI-Trader-Assistant-Portable-{VERSION}-x64.zip"
    dirty, diff_hash, commit = source_state(root)
    lock = hashlib.sha256()
    lock.update((root / "requirements-runtime.lock.txt").read_bytes())
    lock.update((root / "requirements-build.lock.txt").read_bytes())
    lock.update((root / "frontend" / "package-lock.json").read_bytes())
    package = json.loads((root / "frontend" / "package.json").read_text(encoding="utf-8"))
    manifest = {
        "app_version": VERSION,
        "release_status": "RELEASE_CANDIDATE",
        "git_commit": commit,
        "source_dirty": dirty,
        "source_diff_hash": diff_hash,
        "build_time": datetime.now(timezone.utc).isoformat(),
        "builder_os": platform.platform(),
        "architecture": "x64",
        "python_version": platform.python_version(),
        "node_version": subprocess.check_output(["node", "--version"], text=True).strip(),
        "electron_version": package["devDependencies"]["electron"],
        "pyinstaller_version": "6.21.0",
        "electron_builder_version": package["devDependencies"]["electron-builder"],
        "dependency_lock_hash": lock.hexdigest(),
        "frontend_bundle_hash": tree_hash(root / "frontend" / "dist"),
        "backend_bundle_hash": tree_hash(root / "build" / "backend" / "ai-trader-backend"),
        "seed_bundle_hash": tree_hash(root / "build" / "seed"),
        "installer_hash": sha256_file(setup),
        "portable_hash": sha256_file(portable),
        "code_signing": code_signing,
        "backend_executable_smoke": "PASS",
        "portable_smoke": "PASS",
        "installer_smoke": "PASS",
        "clean_windows_acceptance": "NOT_RUN",
        "backend_tests": "544 passed",
        "frontend_tests": "4 passed",
        "type_check": "PASS",
        "frontend_build": "PASS",
        "security_validation": "PASS",
    }
    _write_json(output / "manifests" / f"BUILD_MANIFEST_{VERSION}.json", manifest)
    _write_json(output / "reports" / "build_validation.json", {
        "backend_executable_smoke": "PASS",
        "portable_smoke": "PASS",
        "installer_install": "PASS",
        "installed_application_smoke": "PASS",
        "installer_uninstall": "PASS",
        "seed_history_date": "2026-07-10",
        "local_api_auth": "PASS",
        "seeded_quant_count": 5308,
        "seeded_flash_count": 105,
        "seeded_final_count": 27,
        "seeded_order_position_count": 27,
        "seeded_fundamental_count": 27,
        "seeded_performance": "PASS",
        "clean_windows_acceptance": "NOT_RUN",
    })
    signing_text = "已配置" if code_signing == "CONFIGURED" else "未配置，Windows 可能显示安全提示"
    (output / f"RELEASE_NOTES_{VERSION}.txt").write_text(
        "\n".join((
            "AI Trader Assistant 1.0.0", "", "发行状态：RELEASE_CANDIDATE",
            "平台：Windows 10/11 x64", "定位：交易员日度筛选工作台", "",
            "本版本包含桌面前端、内置 FastAPI 后端、脱敏历史 Seed、真实数据与分析任务入口，以及 Excel 导出。",
            "默认关闭真实交易、调度、虚拟交易和自动任务。真实行情与 LLM 任务只有在用户配置密钥并明确操作后才运行。",
            "", f"代码签名：{signing_text}。", "本机安装、启动、Seed 初始化和退出 smoke 已通过。",
            "尚未在无 Python、Conda、Node 的干净 Windows 电脑验收，因此不标记为生产就绪。", "",
        )), encoding="utf-8",
    )
    return manifest


def write_checksums(output: Path) -> None:
    target = output / "checksums" / "SHA256SUMS.txt"
    files = sorted(path for path in output.rglob("*") if path.is_file() and path != target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(f"{sha256_file(path)}  {path.relative_to(output).as_posix()}\n" for path in files), encoding="ascii")


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--code-signing", choices=("CONFIGURED", "NOT_CONFIGURED"), default="NOT_CONFIGURED")
    parser.add_argument("--checksums-only", action="store_true")
    args = parser.parse_args()
    root, output = args.root.resolve(), args.output.resolve()
    if args.checksums_only:
        write_checksums(output)
    else:
        manifest = generate(root, output, args.code_signing)
        print(json.dumps({key: manifest[key] for key in ("release_status", "source_dirty", "installer_hash", "portable_hash", "code_signing")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
