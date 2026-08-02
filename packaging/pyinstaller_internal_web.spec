from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
hiddenimports = []
for package in (
    "agents", "alerts", "backend", "database", "datasource", "datasource.ifind",
    "fundamentals", "intraday_monitor", "llm_gateway", "market_review", "memory",
    "midday", "model_validation", "order_price", "position_sizing", "post_close", "quant",
    "recheck", "research", "review", "screening", "services", "temporal", "trader_demo",
    "trading", "jwt", "argon2",
):
    hiddenimports += collect_submodules(package)
hiddenimports += [
    "sqlalchemy.dialects.sqlite", "uvicorn.logging", "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto", "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on",
    "openpyxl.cell._writer", "tushare", "baostock",
]
hiddenimports = [item for item in hiddenimports if item != "backend.internal_web_service"]

# The internal-Web package carries only its own schema.  Provider configuration
# is loaded from the ACL-protected ProgramData config directory when required;
# credentials and provider probe files must never enter this package.
common_datas = [
    (str(ROOT / "frontend" / "dist"), "frontend/dist"),
    (str(ROOT / "config" / "internal_web.yaml"), "config"),
]
for package in ("akshare", "baostock", "tushare", "openpyxl"):
    common_datas.extend(collect_data_files(package, include_py_files=False))

conda_bin = Path(sys.prefix) / "Library" / "bin"
common_binaries = [
    (str(path), ".")
    for name in ("libssl-3-x64.dll", "libcrypto-3-x64.dll")
    if (path := conda_bin / name).is_file()
]
common_excludes = [
    "pytest", "tests", "IPython", "jupyter", "notebook", "tkinter",
    "PyQt5", "PyQt6", "PySide2", "PySide6",
    "torch", "torchvision", "torchaudio", "transformers", "tensorflow", "keras",
    "matplotlib", "sklearn", "sentence_transformers",
]

web_analysis = Analysis(
    [str(ROOT / "backend" / "internal_web_entry.py")], pathex=[str(ROOT)], binaries=common_binaries, datas=common_datas,
    hiddenimports=hiddenimports, hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=common_excludes, noarchive=False,
)
web_pyz = PYZ(web_analysis.pure)
web_exe = EXE(
    web_pyz, web_analysis.scripts, web_analysis.binaries, web_analysis.datas, [],
    name="ai-trader-internal-web", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=True,
)

service_analysis = Analysis(
    [str(ROOT / "backend" / "internal_web_service.py")], pathex=[str(ROOT)], binaries=[], datas=[],
    hiddenimports=["win32timezone", "servicemanager", "win32api", "win32con", "win32event", "win32job", "win32service", "win32serviceutil"],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=common_excludes, noarchive=False,
)
service_pyz = PYZ(service_analysis.pure)
service_exe = EXE(
    service_pyz, service_analysis.scripts, [],
    name="AITraderInternalService", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=True, exclude_binaries=True,
)
service_collect = COLLECT(
    service_exe, service_analysis.binaries, service_analysis.datas,
    strip=False, upx=False, name="AITraderInternalService",
)
