# Packaging Guide

Phase 15 provides packaging preparation, not a signed production installer.

Before packaging, run:

```powershell
python scripts/check_security_config.py
python -m pytest
python -m compileall .
cd frontend
npm run typecheck
npm run build
cd ..
```

Backend packaging skeleton:

```powershell
python scripts/package_backend_pyinstaller.py
```

If PyInstaller is not installed, the script prints an install hint after safety and test validation.

Frontend and Electron packaging skeleton:

```powershell
python scripts/package_windows_app.py
```

Or from `frontend/`:

```powershell
npm run package:win
```

Artifact intent:

- Backend executable layout: `dist/backend`.
- Electron local Windows shell: `frontend/release`.
- Runtime config remains outside packaged binaries.
- No API Key, password, token, or secret should be embedded in artifacts.

The first packaged version expects the backend to run as a local process and the Electron shell to connect to `http://127.0.0.1:8000`.
