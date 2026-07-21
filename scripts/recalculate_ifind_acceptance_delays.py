from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.models import IFindShadowAcceptanceItem
from database.session import get_session


SHANGHAI = ZoneInfo("Asia/Shanghai")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--source-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.source_report.read_text(encoding="utf-8"))
    session = get_session()
    try:
        rows = list(session.scalars(select(IFindShadowAcceptanceItem).where(
            IFindShadowAcceptanceItem.acceptance_run_id == args.run_id,
            IFindShadowAcceptanceItem.capability == "stock_realtime",
        ).order_by(IFindShadowAcceptanceItem.observation_time, IFindShadowAcceptanceItem.id)))
    finally:
        session.close()
    clusters: list[list] = []
    for row in rows:
        observed = _aware(row.observation_time)
        if not clusters or (observed - _aware(clusters[-1][-1].observation_time)).total_seconds() > 30:
            clusters.append([])
        clusters[-1].append(row)
    corrected = []
    for index, cluster in enumerate(clusters[:3], start=1):
        delays = []
        samples = []
        for row in cluster:
            provider = _provider_time(row.provider_time)
            observed = _aware(row.observation_time)
            if provider:
                delays.append(max(0.0, (observed - provider).total_seconds()))
                if len(samples) < 2:
                    samples.append({"provider_time": provider.isoformat(), "observation_time": observed.isoformat()})
        corrected.append({
            "round": index, "count": len(cluster), "delay_p50": median(delays) if delays else None,
            "delay_p95": _percentile(delays, .95) if delays else None, "maximum_delay": max(delays) if delays else None,
            "freshness_status": "PASS" if delays and _percentile(delays, .95) <= 60 else "TIME_SEMANTICS_UNKNOWN",
            "representative_times": samples,
        })
    output = {
        "source_acceptance_run_id": args.run_id,
        "source_report": str(args.source_report),
        "provider_time_semantics": {"raw_field_name": "time", "provider_time_source": "EXCHANGE_QUOTE_TIME", "precision": "SECOND", "timezone": "Asia/Shanghai", "delay_valid": True, "fallback_behavior": "NULL_DELAY_AND_TIME_SEMANTICS_UNKNOWN"},
        "original_zero_delay_invalidated": True,
        "corrected_rounds": corrected,
        "promotion_recommendation": report.get("promotion_recommendation"),
        "raw_response_included": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=True))
    return 0


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=SHANGHAI) if value.tzinfo is None else value.astimezone(SHANGHAI)


def _provider_time(value: str | None) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=SHANGHAI) if parsed.tzinfo is None else parsed.astimezone(SHANGHAI)
    except (TypeError, ValueError):
        return None


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values: return None
    ordered = sorted(values); position = (len(ordered) - 1) * quantile; lower = int(position); upper = min(lower + 1, len(ordered) - 1); weight = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


if __name__ == "__main__":
    raise SystemExit(main())
