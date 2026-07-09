from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


ROOT_BOOTSTRAP = Path(__file__).resolve().parents[1]
if str(ROOT_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(ROOT_BOOTSTRAP))


REPORT_PATH = Path("data/reports/network_diagnostics_report.json")
PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)
TARGET_URLS = (
    "https://www.baidu.com",
    "https://www.eastmoney.com",
    "https://push2.eastmoney.com",
)


def redact_proxy_url(value: str | None) -> str:
    if not value:
        return ""
    try:
        parts = urlsplit(value)
    except ValueError:
        return "<redacted>" if "@" in value else value
    if "@" not in parts.netloc:
        return value
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    if parts.username:
        netloc = f"{parts.username}:<redacted>@{host}"
    else:
        netloc = f"<redacted>@{host}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def collect_proxy_env() -> dict[str, str]:
    return {key: redact_proxy_url(os.environ.get(key)) for key in PROXY_ENV_KEYS if os.environ.get(key)}


def proxy_env_detected() -> bool:
    return any(os.environ.get(key) for key in PROXY_ENV_KEYS)


@contextmanager
def temporary_no_proxy():
    original = {key: os.environ.get(key) for key in PROXY_ENV_KEYS}
    try:
        for key in PROXY_ENV_KEYS:
            os.environ.pop(key, None)
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def run_network_diagnostics(
    no_proxy: bool = False,
    timeout_seconds: int = 10,
    output: str | Path = REPORT_PATH,
) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc)
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    proxy_env_before = collect_proxy_env()
    report: dict[str, Any] = {
        "python_version": sys.version.replace("\n", " "),
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "proxy_env_detected": bool(proxy_env_before),
        "proxy_env": proxy_env_before,
        "no_proxy": no_proxy,
        "timeout_seconds": timeout_seconds,
        "targets": [],
        "errors_sample": [],
        "started_at": started_at.isoformat(),
        "finished_at": None,
        "duration_seconds": 0,
        "report_path": str(output_path),
    }

    context = temporary_no_proxy() if no_proxy else _null_context()
    with context:
        for url in TARGET_URLS:
            result = _test_url(url, timeout_seconds)
            report["targets"].append(result)
            if not result["ok"] and len(report["errors_sample"]) < 20:
                report["errors_sample"].append(
                    {
                        "url": url,
                        "error_type": result.get("error_type"),
                        "error_message": result.get("error_message"),
                    }
                )

    finished_at = datetime.now(timezone.utc)
    report["finished_at"] = finished_at.isoformat()
    report["duration_seconds"] = round((finished_at - started_at).total_seconds(), 3)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _test_url(url: str, timeout_seconds: int) -> dict[str, Any]:
    started = time.monotonic()
    request = urllib.request.Request(url, headers={"User-Agent": "AI-Trader-Assistant-Diagnostics/0.3"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return {
                "url": url,
                "ok": True,
                "status_code": getattr(response, "status", None),
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "error_type": None,
                "error_message": None,
            }
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {
            "url": url,
            "ok": False,
            "status_code": None,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "error_type": exc.__class__.__name__,
            "error_message": str(exc),
        }


@contextmanager
def _null_context():
    yield


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose real data-source network connectivity.")
    parser.add_argument("--no-proxy", action="store_true", help="Clear proxy env vars only for this process.")
    parser.add_argument("--timeout", type=int, default=10)
    parser.add_argument("--output", default=str(REPORT_PATH))
    args = parser.parse_args()

    report = run_network_diagnostics(
        no_proxy=args.no_proxy,
        timeout_seconds=args.timeout,
        output=args.output,
    )
    ok_count = sum(1 for target in report["targets"] if target["ok"])
    print(
        "summary: "
        f"proxy_env_detected={report['proxy_env_detected']} "
        f"no_proxy={report['no_proxy']} "
        f"ok_count={ok_count}/{len(report['targets'])} "
        f"report_path={report['report_path']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
