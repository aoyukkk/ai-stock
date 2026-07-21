from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.core.config import load_app_config
from database.session import get_session, init_db
from datasource.ifind.probe import IFindCapabilityProbe


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a bounded and sanitized iFinD capability audit.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Run zero-call capability discovery (default).")
    mode.add_argument("--real-probe", action="store_true", help="Run only when every safety gate passes.")
    parser.add_argument("--transport", choices=("auto", "http", "sdk", "disabled"), default="auto")
    parser.add_argument("--categories", help="Comma-separated capability categories.")
    parser.add_argument("--max-calls", type=int, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("data/reports/ifind"))
    parser.add_argument("--sample-stock-count", type=int, default=None)
    parser.add_argument("--skip-packaging-audit", action="store_true")
    parser.add_argument("--stop-on-login-failure", action="store_true")
    parser.add_argument("--stop-on-auth-failure", action="store_true")
    args = parser.parse_args()

    app_config = load_app_config()
    config = dict((app_config.config_files.get("ifind_probe") or {}).get("ifind_probe") or {})
    if args.max_calls is not None:
        config["max_total_calls"] = args.max_calls
    if args.sample_stock_count is not None:
        config["sample_stock_count"] = args.sample_stock_count
    config["skip_packaging_audit"] = bool(args.skip_packaging_audit)
    init_db()
    session = get_session()
    try:
        report = IFindCapabilityProbe(session, config, output_dir=args.output_dir).run(
            real_probe=bool(args.real_probe),
            categories={item.strip().upper() for item in args.categories.split(",") if item.strip()} if args.categories else None,
            stop_on_login_failure=bool(args.stop_on_login_failure or args.stop_on_auth_failure),
            transport=args.transport,
        )
        print(json.dumps({
            "audit_id": report.audit_id, "mode": report.mode, "status": report.overall_status,
            "sdk_status": report.environment.sdk_status, "login_status": report.authentication["login_status"],
            "transport": report.authentication.get("transport"), "auth_calls": report.authentication.get("auth_calls", 0),
            "actual_calls": report.call_summary.actual_calls, "report_path": report.report_path,
            "call_summary_path": report.call_summary_path,
        }, ensure_ascii=False))
    finally:
        session.close()


if __name__ == "__main__":
    main()
