from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    executable = args.executable.resolve()
    if not executable.is_file():
        raise FileNotFoundError(executable)
    with tempfile.TemporaryDirectory(prefix="ai-trader-backend-smoke-") as temporary:
        root = Path(temporary)
        database = root / "data" / "ai_trader.db"
        database.parent.mkdir(parents=True)
        shutil.copy2(args.seed.resolve(), database)
        shutil.copytree(args.config.resolve(), root / "config")
        seed_root = args.seed.resolve().parents[1]
        for name in ("cache", "outputs", "reports"):
            source = seed_root / name
            if source.is_dir():
                shutil.copytree(source, root / ("diagnostics" if name == "reports" else name))
        port = _available_port()
        token = secrets.token_urlsafe(40)
        env = os.environ.copy()
        env.update({
            "AI_TRADER_USER_DATA_DIR": str(root),
            "AI_TRADER_DB_PATH": str(database),
            "AI_TRADER_CACHE_DIR": str(root / "cache"),
            "AI_TRADER_OUTPUT_DIR": str(root / "outputs"),
            "AI_TRADER_LOG_DIR": str(root / "logs"),
            "AI_TRADER_BACKUP_DIR": str(root / "backups"),
            "AI_TRADER_CONFIG_DIR": str(root / "config"),
            "AI_TRADER_DESKTOP_MODE": "true",
            "AI_TRADER_ENV": "production",
            "AI_TRADER_PORT": str(port),
            "AI_TRADER_LOCAL_API_TOKEN": token,
            "ENABLE_REAL_TRADING": "false",
            "SCHEDULER_ENABLED": "false",
            "PAPER_TRADING_ENABLED": "false",
        })
        process = subprocess.Popen(
            [str(executable)],
            env=env,
            cwd=root,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        base = f"http://127.0.0.1:{port}"
        try:
            _wait_health(base, process)
            headers = {"X-AI-Trader-Token": token}
            version = httpx.get(f"{base}/api/version", headers=headers, timeout=5).json()
            dates = httpx.get(f"{base}/api/workbench/available-dates", headers=headers, timeout=10).json()
            unauthorized = httpx.get(f"{base}/api/version", timeout=5)
            if unauthorized.status_code != 401:
                raise RuntimeError("LOCAL_AUTH_NOT_ENFORCED")
            if version.get("data", {}).get("app_version") != "1.0.0":
                raise RuntimeError("VERSION_MISMATCH")
            items = dates.get("data", {}).get("items", [])
            if not any(item.get("trade_date") == "2026-07-10" for item in items):
                raise RuntimeError("SEEDED_HISTORY_NOT_VISIBLE")
            checks = {
                "status": ("/api/workbench/status?trade_date=2026-07-10", "source_mode", "DATABASE"),
                "quant": ("/api/workbench/quant/results?trade_date=2026-07-10&page=1&page_size=5", "total", 5308),
                "flash": ("/api/workbench/flash/results?trade_date=2026-07-10&page=1&page_size=5", "total", 105),
                "final": ("/api/workbench/final/results?trade_date=2026-07-10", "items", 27),
                "orders": ("/api/workbench/order-position/results?trade_date=2026-07-10", "items", 27),
                "fundamentals": ("/api/workbench/fundamentals/results?trade_date=2026-07-10", "items", 27),
            }
            for name, (endpoint, field, expected) in checks.items():
                response = httpx.get(f"{base}{endpoint}", headers=headers, timeout=30)
                if response.status_code != 200:
                    raise RuntimeError(f"SEEDED_{name.upper()}_HTTP_{response.status_code}")
                value = response.json().get("data", {}).get(field)
                actual = len(value) if isinstance(value, list) else value
                if actual != expected:
                    raise RuntimeError(f"SEEDED_{name.upper()}_MISMATCH:{actual}")
            performance = httpx.get(f"{base}/api/workbench/performance/runs", headers=headers, timeout=30).json()
            if not performance.get("data", {}).get("items"):
                raise RuntimeError("SEEDED_PERFORMANCE_NOT_VISIBLE")
            httpx.post(f"{base}/api/runtime/shutdown", headers=headers, timeout=5)
            process.wait(timeout=10)
            print(json.dumps({
                "passed": True, "version": "1.0.0", "history_date": "2026-07-10",
                "local_auth": "PASS", "quant": 5308, "flash": 105, "final": 27,
                "orders": 27, "positions": 27, "fundamentals": 27, "performance": "PASS",
            }))
            return 0
        finally:
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: process.kill()


def _available_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_health(base: str, process: subprocess.Popen, timeout: float = 90) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            raise RuntimeError(f"BACKEND_EXITED:{process.returncode}:{_redact(output)[-2000:]}")
        try:
            if httpx.get(f"{base}/health", timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    raise TimeoutError("BACKEND_HEALTH_TIMEOUT")


def _redact(text: str) -> str:
    for name in ("TUSHARE_TOKEN", "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "AI_TRADER_LOCAL_API_TOKEN"):
        value = os.getenv(name, "")
        if value:
            text = text.replace(value, "[REDACTED]")
    return text.replace("\r", " ").replace("\n", " | ")


if __name__ == "__main__":
    raise SystemExit(main())
