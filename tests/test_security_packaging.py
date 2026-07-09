from pathlib import Path

from scripts.check_security_config import run_checks


ROOT = Path(__file__).resolve().parents[1]


def test_env_templates_keep_real_trading_disabled() -> None:
    root_env = (ROOT / ".env.example").read_text(encoding="utf-8")
    frontend_env = (ROOT / "frontend" / ".env.example").read_text(encoding="utf-8")

    assert "ENABLE_REAL_TRADING=false" in root_env
    assert "AI_AUTO_REAL_ORDER_ENABLED=false" in root_env
    assert "VITE_ENABLE_REAL_TRADING=false" in frontend_env


def test_packaging_configs_do_not_reference_runtime_env_file() -> None:
    packaging_files = [
        ROOT / "packaging" / "pyinstaller_backend.spec",
        ROOT / "packaging" / "electron-builder.config.js",
        ROOT / "frontend" / "electron-builder.config.js",
    ]

    for path in packaging_files:
        text = path.read_text(encoding="utf-8")
        assert ".env" not in text
        assert "node_modules" not in text or path.name == "pyinstaller_backend.spec"


def test_deployment_docs_state_real_trading_is_forbidden() -> None:
    docs = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "deployment").glob("*.md")
    )

    assert "禁止实盘自动交易" in docs or "No automatic real-market order placement" in docs
    assert "Mock Provider" in docs
    assert "Mock LLM" in docs


def test_security_check_detects_real_trading_true(tmp_path: Path) -> None:
    _write_minimal_tree(tmp_path, enable_real_trading="true")

    report = run_checks(tmp_path)

    assert not report.ok
    assert any("ENABLE_REAL_TRADING" in error for error in report.errors)


def test_security_check_accepts_minimal_safe_tree(tmp_path: Path) -> None:
    _write_minimal_tree(tmp_path, enable_real_trading="false")

    report = run_checks(tmp_path)

    assert report.ok


def _write_minimal_tree(root: Path, enable_real_trading: str) -> None:
    (root / "config").mkdir(parents=True)
    (root / "frontend").mkdir()
    (root / "packaging").mkdir()

    (root / ".env.example").write_text(
        f"ENABLE_REAL_TRADING={enable_real_trading}\nAI_AUTO_REAL_ORDER_ENABLED=false\n",
        encoding="utf-8",
    )
    (root / "frontend" / ".env.example").write_text(
        "VITE_ENABLE_REAL_TRADING=false\n",
        encoding="utf-8",
    )
    (root / "config" / "system.yaml").write_text(
        "safety:\n  enable_real_trading: false\n",
        encoding="utf-8",
    )
    (root / "config" / "virtual_trading.yaml").write_text(
        "virtual_trading:\n  real_trading_enabled: false\n",
        encoding="utf-8",
    )
    (root / "config" / "paper_trading.yaml").write_text(
        "paper_trading:\n  real_trading_enabled: false\n",
        encoding="utf-8",
    )
    (root / "config" / "models.yaml").write_text(
        "llm:\n  mock_only: true\n",
        encoding="utf-8",
    )
    (root / "config" / "llm.yaml").write_text(
        "llm:\n  mock_only: true\n",
        encoding="utf-8",
    )
    (root / "config" / "data_sources.yaml").write_text(
        "data_sources:\n  market_fallback:\n    - provider: mock\n      enabled: true\n",
        encoding="utf-8",
    )
    for path in (
        root / "packaging" / "pyinstaller_backend.spec",
        root / "packaging" / "electron-builder.config.js",
        root / "frontend" / "electron-builder.config.js",
    ):
        path.write_text("files = []\n", encoding="utf-8")
