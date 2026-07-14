import importlib
from pathlib import Path


SCRIPT_MODULES = [
    "scripts.check_environment",
    "scripts.init_local_env",
    "scripts.check_security_config",
    "scripts.dev_start_backend",
    "scripts.dev_start_frontend",
    "scripts.dev_start_all",
    "scripts.package_backend_pyinstaller",
    "scripts.package_windows_app",
    "scripts.run_final_smoke",
    "scripts.run_all_checks",
    "scripts.generate_v1_release_metadata",
    "scripts.scan_v1_release",
    "scripts.smoke_backend_executable",
]


def test_phase15_scripts_import_without_side_effects() -> None:
    data_marker = Path("data/import_side_effect_marker.txt")
    logs_marker = Path("logs/import_side_effect_marker.txt")

    assert not data_marker.exists()
    assert not logs_marker.exists()

    for module_name in SCRIPT_MODULES:
        module = importlib.import_module(module_name)
        assert hasattr(module, "main") or hasattr(module, "run_checks")

    assert not data_marker.exists()
    assert not logs_marker.exists()


def test_startup_scripts_keep_local_only_defaults() -> None:
    backend = importlib.import_module("scripts.dev_start_backend")
    frontend = importlib.import_module("scripts.dev_start_frontend")
    all_start = importlib.import_module("scripts.dev_start_all")

    assert backend.DEFAULT_HOST == "127.0.0.1"
    assert backend.DEFAULT_PORT == "8000"
    assert "127.0.0.1:5173" in Path(frontend.__file__).read_text(encoding="utf-8")
    assert "real_trading_enabled=false" in Path(all_start.__file__).read_text(encoding="utf-8")
