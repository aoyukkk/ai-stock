# Packaging Skeleton

This directory contains packaging configuration skeletons for Phase 15.

- `pyinstaller_backend.spec` prepares a backend executable layout under `dist/backend`.
- `electron-builder.config.js` mirrors the Electron packaging intent for a Windows local desktop shell.

The first packaged version remains local, mock-only, and advisory. Runtime configuration is kept outside generated binaries and installers.
