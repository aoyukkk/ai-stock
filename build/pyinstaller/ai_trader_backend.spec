# Source-controlled PyInstaller manifest retained under build/ for the desktop
# release contract tests. Generated binaries and temporary build outputs are not
# kept in this directory.

from PyInstaller.utils.hooks import collect_submodules


hiddenimports = []
for package in (
    "backend",
    "database",
    "datasource",
    "quant",
    "research",
    "fundamentals",
    "llm_gateway",
    "order_price",
    "position_sizing",
    "review",
    "trader_demo",
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
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ai_trader_backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)
