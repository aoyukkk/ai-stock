# PyInstaller backend packaging skeleton for local Windows deployment.
# Runtime configuration remains external and is not bundled into the executable.

from PyInstaller.utils.hooks import collect_submodules


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
    ["backend/main.py"],
    pathex=["."],
    binaries=[],
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
