from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


REQUIRED_DIRECTORIES = [
    "backend",
    "frontend",
    "agents",
    "quant",
    "order_price",
    "datasource",
    "llm_gateway",
    "memory",
    "trading",
    "database",
    "backtest",
    "review",
    "config",
    "docs",
    "tests",
    "logs",
    "scripts",
]


REQUIRED_FILES = [
    "README.md",
    "pyproject.toml",
    "requirements.txt",
    ".env.example",
    ".gitignore",
]


def test_required_directories_exist() -> None:
    missing = [path for path in REQUIRED_DIRECTORIES if not (ROOT / path).is_dir()]

    assert not missing, f"Missing required directories: {missing}"


def test_required_root_files_exist() -> None:
    missing = [path for path in REQUIRED_FILES if not (ROOT / path).is_file()]

    assert not missing, f"Missing required files: {missing}"
