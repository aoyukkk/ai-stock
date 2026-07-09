from __future__ import annotations

import sys
from pathlib import Path

ROOT_BOOTSTRAP = Path(__file__).resolve().parents[1]
if str(ROOT_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(ROOT_BOOTSTRAP))

from scripts._common import (
    FRONTEND_DIR,
    ROOT_DIR,
    CheckReport,
    env_value,
    has_secret_like_literal,
    is_false,
    is_true,
    iter_files,
    load_yaml,
    print_report,
    read_env_file,
)


SENSITIVE_ENV_PARTS = ("API_KEY", "PASSWORD", "SECRET", "TOKEN", "PASSPHRASE")
SAFE_PLACEHOLDERS = {"", "mock", "disabled", "changeme", "placeholder", "example"}


def run_checks(root: Path = ROOT_DIR) -> CheckReport:
    report = CheckReport()

    _check_env_real_trading(root, report)
    _check_frontend_env(root, report)
    _check_config_files(root, report)
    _check_sensitive_env_values(root, report)
    _check_source_literals(root, report)
    _check_packaging_configs(root, report)
    _check_packaging_outputs(root, report)

    if report.ok:
        report.add_info("Security config checks passed")
    return report


def _check_env_real_trading(root: Path, report: CheckReport) -> None:
    value = env_value(root, "ENABLE_REAL_TRADING")
    if is_false(value):
        report.add_info("ENABLE_REAL_TRADING=false")
    else:
        report.add_error("ENABLE_REAL_TRADING must be false")


def _check_frontend_env(root: Path, report: CheckReport) -> None:
    frontend_env = read_env_file(root / "frontend" / ".env.example")
    if is_false(frontend_env.get("VITE_ENABLE_REAL_TRADING")):
        report.add_info("VITE_ENABLE_REAL_TRADING=false")
    else:
        report.add_error("frontend VITE_ENABLE_REAL_TRADING must be false")


def _check_config_files(root: Path, report: CheckReport) -> None:
    system = load_yaml(root / "config" / "system.yaml")
    virtual = load_yaml(root / "config" / "virtual_trading.yaml")
    paper = load_yaml(root / "config" / "paper_trading.yaml")
    models = load_yaml(root / "config" / "models.yaml")
    llm = load_yaml(root / "config" / "llm.yaml")
    data_sources = load_yaml(root / "config" / "data_sources.yaml")

    if system.get("safety", {}).get("enable_real_trading") is False:
        report.add_info("config safety.enable_real_trading=false")
    else:
        report.add_error("config safety.enable_real_trading must be false")

    if virtual.get("virtual_trading", {}).get("real_trading_enabled") is False:
        report.add_info("virtual_trading.real_trading_enabled=false")
    else:
        report.add_error("virtual_trading.real_trading_enabled must be false")

    if paper.get("paper_trading", {}).get("real_trading_enabled") is False:
        report.add_info("paper_trading.real_trading_enabled=false")
    else:
        report.add_error("paper_trading.real_trading_enabled must be false")

    if models.get("llm", {}).get("mock_only") is True:
        report.add_info("llm.mock_only=true")
    else:
        report.add_error("llm.mock_only must be true")

    if llm.get("llm", {}).get("mock_only") is True:
        report.add_info("config/llm.yaml llm.mock_only=true")
    else:
        report.add_error("config/llm.yaml llm.mock_only must be true")

    provider_configs = list(_iter_provider_configs(data_sources))
    enabled_real_sources = sorted({
        provider
        for provider, enabled, _manual_only in provider_configs
        if provider != "mock" and enabled
    })
    unsafe_real_sources = sorted({
        provider
        for provider, enabled, manual_only in provider_configs
        if provider != "mock" and enabled and not manual_only
    })
    mock_enabled = any(provider == "mock" and enabled for provider, enabled, _ in provider_configs)
    if mock_enabled and not unsafe_real_sources:
        if enabled_real_sources:
            report.add_info(f"manual debug data sources enabled: {enabled_real_sources}")
        else:
            report.add_info("data sources are mock-only")
    else:
        report.add_error(f"non-manual real data sources enabled: {unsafe_real_sources}")


def _iter_provider_configs(value, inherited_manual_only: bool = False):
    if isinstance(value, dict):
        manual_only = inherited_manual_only or bool(value.get("manual_only", False))
        if "provider" in value:
            yield str(value.get("provider")), bool(value.get("enabled", False)), manual_only
        elif any(key in value for key in ("akshare", "baostock", "ifind", "tushare")):
            for provider_name in ("akshare", "baostock", "ifind", "tushare"):
                provider_value = value.get(provider_name)
                if isinstance(provider_value, dict) and "enabled" in provider_value:
                    yield (
                        provider_name,
                        bool(provider_value.get("enabled", False)),
                        manual_only or bool(provider_value.get("manual_only", False)),
                    )
        for child in value.values():
            yield from _iter_provider_configs(child, manual_only)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_provider_configs(child, inherited_manual_only)


def _check_sensitive_env_values(root: Path, report: CheckReport) -> None:
    env_path = root / ".env"
    if not env_path.exists():
        report.add_warning(".env not found; using templates for safety checks")
        return

    values = read_env_file(env_path)
    for key, value in values.items():
        if any(part in key.upper() for part in SENSITIVE_ENV_PARTS):
            normalized = value.strip().lower()
            if normalized not in SAFE_PLACEHOLDERS:
                report.add_error(f"sensitive environment value is populated: {key}")


def _check_source_literals(root: Path, report: CheckReport) -> None:
    roots = [
        root / "backend",
        root / "datasource",
        root / "llm_gateway",
        root / "agents",
        root / "frontend" / "src",
        root / "scripts",
    ]
    for path in iter_files(roots, suffixes={".py", ".ts", ".vue", ".js"}):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if has_secret_like_literal(text):
            report.add_error(f"secret-like literal found in {path.relative_to(root)}")


def _check_packaging_configs(root: Path, report: CheckReport) -> None:
    configs = [
        root / "packaging" / "pyinstaller_backend.spec",
        root / "packaging" / "electron-builder.config.js",
        root / "frontend" / "electron-builder.config.js",
    ]
    for path in configs:
        if not path.exists():
            report.add_error(f"packaging config missing: {path.relative_to(root)}")
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if ".env" in text:
            report.add_error(f"packaging config references runtime env file: {path.relative_to(root)}")
        if has_secret_like_literal(text):
            report.add_error(f"secret-like literal found in packaging config: {path.relative_to(root)}")
    report.add_info("packaging configs checked")


def _check_packaging_outputs(root: Path, report: CheckReport) -> None:
    output_roots = (
        root / "dist",
        root / "frontend" / "release",
        root / "frontend" / "dist",
        root / "frontend" / "dist-electron",
    )
    sensitive_names = {".env", "id_rsa", "id_ed25519"}
    for path in iter_files(output_roots):
        lower_name = path.name.lower()
        if lower_name in sensitive_names or lower_name.endswith((".key", ".pem")):
            report.add_error(f"sensitive file found in packaging output: {path.relative_to(root)}")
    report.add_info("packaging outputs checked")


def main() -> int:
    report = run_checks()
    print_report(report)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
