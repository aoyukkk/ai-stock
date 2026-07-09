import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


REQUIRED_FRONTEND_FILES = [
    "README.md",
    "package.json",
    "index.html",
    "vite.config.ts",
    "tsconfig.json",
    "tsconfig.node.json",
    ".env.example",
    "electron/main.ts",
    "electron/preload.ts",
    "src/main.ts",
    "src/App.vue",
    "src/router/index.ts",
    "src/layouts/MainLayout.vue",
    "src/components/SafetyBanner.vue",
    "src/components/ConfigEditorGroup.vue",
    "src/api/config.ts",
    "src/stores/configStore.ts",
    "src/stores/languageStore.ts",
    "src/pages/Dashboard.vue",
    "src/pages/SystemStatus.vue",
    "src/pages/SystemConfig.vue",
    "src/pages/ModelManagement.vue",
    "src/pages/VirtualTrading.vue",
    "src/pages/MemoryConsole.vue",
]


def test_frontend_phase13_files_exist() -> None:
    missing = [path for path in REQUIRED_FRONTEND_FILES if not (FRONTEND / path).is_file()]

    assert not missing, f"Missing frontend files: {missing}"


def test_package_scripts_and_env_defaults_are_safe() -> None:
    package = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))
    env_example = (FRONTEND / ".env.example").read_text(encoding="utf-8")

    for script in ("dev", "dev:web", "dev:electron", "build", "typecheck", "preview"):
        assert script in package["scripts"]

    assert "VITE_ENABLE_REAL_TRADING=false" in env_example
    assert "VITE_API_BASE_URL=http://127.0.0.1:8000" in env_example


def test_frontend_has_no_real_trading_entry_text() -> None:
    forbidden = [
        "Enable Real Trading",
        "Place Real Order",
        "Submit Real Order",
        "Auto Real Trading",
    ]
    source_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (FRONTEND / "src").rglob("*")
        if path.suffix in {".ts", ".vue"}
    )

    for phrase in forbidden:
        assert phrase not in source_text


def test_frontend_phase14_config_persistence_hooks_exist() -> None:
    config_api = (FRONTEND / "src/api/config.ts").read_text(encoding="utf-8")
    config_store = (FRONTEND / "src/stores/configStore.ts").read_text(encoding="utf-8")
    language_store = (FRONTEND / "src/stores/languageStore.ts").read_text(encoding="utf-8")
    system_config = (FRONTEND / "src/pages/SystemConfig.vue").read_text(encoding="utf-8")

    for method in (
        "getEffectiveConfig",
        "getEditableConfig",
        "updateConfigValue",
        "updateConfigBulk",
        "resetConfigValue",
        "getConfigHistory",
    ):
        assert method in config_api

    assert "loadConfig" in config_store
    assert "saveCategory" in config_store
    assert "resetItem" in config_store
    assert "systemConfig.configHistory" in system_config
    assert "config_history" in language_store
    assert "ElMessageBox.confirm" in system_config


def test_frontend_language_selector_supports_chinese() -> None:
    language_store = (FRONTEND / "src/stores/languageStore.ts").read_text(encoding="utf-8")
    main_layout = (FRONTEND / "src/layouts/MainLayout.vue").read_text(encoding="utf-8")
    app = (FRONTEND / "src/App.vue").read_text(encoding="utf-8")
    dashboard = (FRONTEND / "src/pages/Dashboard.vue").read_text(encoding="utf-8")
    virtual_trading = (FRONTEND / "src/pages/VirtualTrading.vue").read_text(encoding="utf-8")

    assert '"zh-CN"' in language_store
    assert "中文" in language_store
    assert "dashboard.backendHealth" in language_store
    assert "virtual.orders" in language_store
    assert "language-select" in main_layout
    assert "setLanguage" in main_layout
    assert "el-config-provider" in app
    assert "language.t('dashboard.title')" in dashboard
    assert "language.t('virtual.orders')" in virtual_trading
