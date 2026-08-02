# PyInstaller backend packaging skeleton for local Windows deployment.
# Runtime configuration remains external and is not bundled into the executable.

from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent
CONDA_BIN = Path(sys.prefix) / "Library" / "bin"
runtime_binaries = [
    (str(CONDA_BIN / name), ".")
    for name in ("libssl-3-x64.dll", "libcrypto-3-x64.dll", "zlib.dll", "zlib1.dll")
    if (CONDA_BIN / name).is_file()
]

hiddenimports = []
for package in (
    "backend",
    "database",
    "datasource",
    "datasource.ifind",
    "services",
    "quant",
    "screening",
    "agents",
    "order_price",
    "trading",
    "alerts",
    "recheck",
    "review",
    "memory",
    "market_review",
    "intraday_monitor",
    "llm_gateway",
):
    hiddenimports += collect_submodules(package)


a = Analysis(
    [str(ROOT / "backend" / "desktop_entry.py")],
    pathex=[str(ROOT)],
    binaries=runtime_binaries,
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["node_modules", "frontend"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ai-trader-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="backend",
)
