from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_BOOTSTRAP = Path(__file__).resolve().parents[1]
if str(ROOT_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(ROOT_BOOTSTRAP))

from scripts.run_real_quant_top500 import run_real_quant_top500
from scripts.run_real_quant_top500 import _parse_bool_arg


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke wrapper for real quant Top500 debug run.")
    parser.add_argument("--provider", choices=["mock", "akshare", "baostock"], default="akshare")
    parser.add_argument("--history-provider", choices=["mock", "akshare", "baostock"], default="baostock")
    parser.add_argument("--top-n", type=int, default=500)
    parser.add_argument("--sample-limit", type=int, default=0)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--trade-date", default=None)
    parser.add_argument("--max-lookback-days", type=int, default=15)
    parser.add_argument("--akshare-no-proxy", action="store_true")
    parser.add_argument("--output", default="data/reports/real_quant_top500_report.json")
    parser.add_argument("--use-cache", type=_parse_bool_arg, default=True)
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--data-fetch-workers", type=int, default=1)
    parser.add_argument("--factor-workers", default="1")
    args = parser.parse_args()

    report = run_real_quant_top500(
        provider=args.provider,
        history_provider=args.history_provider,
        top_n=args.top_n,
        sample_limit=args.sample_limit,
        start_date=args.start_date,
        end_date=args.end_date,
        trade_date=args.trade_date,
        max_lookback_days=args.max_lookback_days,
        akshare_no_proxy=args.akshare_no_proxy,
        output=args.output,
        progress=True,
        use_cache=args.use_cache,
        refresh_cache=args.refresh_cache,
        data_fetch_workers=args.data_fetch_workers,
        factor_workers=args.factor_workers,
    )
    performance = report.get("performance", {})
    print(
        "summary: "
        f"provider={report['provider']} history_provider={report['history_provider']} "
        f"universe_count={report['universe_count']} filtered_count={report['filtered_count']} "
        f"scored_count={report['scored_count']} top_count={report['top_count']} "
        f"no_llm_call_verified={report['no_llm_call_verified']} "
        f"total_seconds={performance.get('total_seconds', 0.0)} "
        f"cache_hit_count={performance.get('cache_hit_count', 0)} "
        f"cache_miss_count={performance.get('cache_miss_count', 0)} "
        f"report_path={report['report_path']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
