from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


EXPECTED_DIRECTORIES = [
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
    "config",
    "tests",
    "docs",
    "logs",
]

EXPECTED_FILES = [
    "README.md",
    "requirements.txt",
    ".env.example",
    ".gitignore",
    "pytest.ini",
]

EXPECTED_PACKAGE_FILES = [
    "backend/__init__.py",
    "agents/__init__.py",
    "quant/__init__.py",
    "order_price/__init__.py",
    "datasource/__init__.py",
    "llm_gateway/__init__.py",
    "memory/__init__.py",
    "trading/__init__.py",
    "database/__init__.py",
    "backtest/__init__.py",
    "tests/__init__.py",
]


def test_expected_directories_exist() -> None:
    for directory in EXPECTED_DIRECTORIES:
        path = PROJECT_ROOT / directory
        assert path.is_dir(), f"Missing directory: {directory}"


def test_expected_root_files_exist() -> None:
    for file_name in EXPECTED_FILES:
        path = PROJECT_ROOT / file_name
        assert path.is_file(), f"Missing file: {file_name}"


def test_python_package_init_files_exist() -> None:
    for file_name in EXPECTED_PACKAGE_FILES:
        path = PROJECT_ROOT / file_name
        assert path.is_file(), f"Missing package file: {file_name}"
