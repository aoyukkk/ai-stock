from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from backend.version import SCHEMA_VERSION


@dataclass(frozen=True)
class DesktopPaths:
    root: Path
    data: Path
    database: Path
    cache: Path
    outputs: Path
    logs: Path
    backups: Path
    config: Path
    secrets: Path
    diagnostics: Path
    temp: Path

    @classmethod
    def from_environment(cls) -> "DesktopPaths":
        root_value = os.getenv("AI_TRADER_USER_DATA_DIR", "").strip()
        if not root_value:
            raise RuntimeError("AI_TRADER_USER_DATA_DIR_REQUIRED")
        root = Path(root_value).expanduser().resolve()
        return cls(
            root=root,
            data=_env_path("AI_TRADER_DATA_DIR", root / "data"),
            database=_env_path("AI_TRADER_DB_PATH", root / "data" / "ai_trader.db"),
            cache=_env_path("AI_TRADER_CACHE_DIR", root / "cache"),
            outputs=_env_path("AI_TRADER_OUTPUT_DIR", root / "outputs"),
            logs=_env_path("AI_TRADER_LOG_DIR", root / "logs"),
            backups=_env_path("AI_TRADER_BACKUP_DIR", root / "backups"),
            config=_env_path("AI_TRADER_CONFIG_DIR", root / "config"),
            secrets=root / "secrets",
            diagnostics=root / "diagnostics",
            temp=root / "temp",
        )

    def create(self) -> None:
        for path in (
            self.root,
            self.data,
            self.database.parent,
            self.cache,
            self.outputs,
            self.logs,
            self.backups,
            self.config,
            self.secrets,
            self.diagnostics,
            self.temp,
        ):
            path.mkdir(parents=True, exist_ok=True)


def prepare_desktop_database(paths: DesktopPaths, *, backup_retention: int = 10) -> dict[str, str | bool]:
    paths.create()
    existed = paths.database.exists()
    backup = backup_database(paths.database, paths.backups) if existed else None

    os.environ["DATABASE_URL"] = f"sqlite:///{paths.database.as_posix()}"
    from database.session import close_db, init_db

    close_db()
    init_db()
    with sqlite3.connect(paths.database) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS desktop_schema_version "
            "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT OR IGNORE INTO desktop_schema_version(version, applied_at) VALUES (?, ?)",
            (SCHEMA_VERSION, datetime.now(timezone.utc).isoformat()),
        )
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            if backup is not None:
                connection.close()
                shutil.copy2(backup, paths.database)
            raise RuntimeError("SQLITE_INTEGRITY_CHECK_FAILED")
        connection.commit()
    prune_backups(paths.backups, backup_retention)
    return {
        "database_created": not existed,
        "backup_created": backup is not None,
        "schema_version": SCHEMA_VERSION,
        "integrity": "ok",
    }


def backup_database(database: Path, backup_dir: Path) -> Path | None:
    if not database.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    digest = hashlib.sha256(database.read_bytes()).hexdigest()[:12]
    destination = backup_dir / f"ai_trader_{stamp}_{digest}.db"
    with sqlite3.connect(database) as source, sqlite3.connect(destination) as target:
        source.backup(target)
    return destination


def prune_backups(backup_dir: Path, retention: int) -> None:
    keep = max(1, int(retention))
    files = sorted(backup_dir.glob("ai_trader_*.db"), key=lambda item: item.stat().st_mtime, reverse=True)
    for path in files[keep:]:
        path.unlink(missing_ok=True)


def _env_path(name: str, default: Path) -> Path:
    value = os.getenv(name, "").strip()
    return Path(value).expanduser().resolve() if value else default.resolve()

