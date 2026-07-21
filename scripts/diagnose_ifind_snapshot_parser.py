from __future__ import annotations

import argparse
import json
import random
import sys
import uuid
from datetime import date, datetime, time
from pathlib import Path
from types import SimpleNamespace

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.runtime_paths import output_root
from database.session import get_session, init_db
from datasource.ifind.http.normalizer import normalize_rows_with_audit
from datasource.ifind.http.errors import IFindHttpError
from midday.adaptive_coverage import market_bucket
from midday.full_a_service import FullAMiddayService
from midday.provider import MiddayIFindCollector
from stock_codes import normalize_ts_code


FIELDS = ("open", "high", "low", "latest", "volume", "amount")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trade-date", type=date.fromisoformat, required=True)
    parser.add_argument("--cutoff-time", type=time.fromisoformat, required=True)
    parser.add_argument("--sizes", type=int, nargs="+", default=[20, 100, 200])
    parser.add_argument("--single-sample", type=int, default=20)
    parser.add_argument("--format-probe", nargs="*", default=[])
    args = parser.parse_args()
    load_dotenv(ROOT / ".env", override=False)
    init_db()
    session = get_session()
    try:
        service = FullAMiddayService(session, output_root=output_root())
        shadow = SimpleNamespace(report_json={})
        _, quant_rows, masters, daily = service._preflight(args.trade_date, shadow)
        universe, _ = service._universe(args.trade_date, quant_rows, masters, daily)
        grouped = {name: [] for name in ("SH_MAIN", "STAR", "SZ_MAIN", "CHINEXT", "BSE")}
        for row in universe:
            grouped[market_bucket(row["stock_code"])].append(row["stock_code"])
        collector = MiddayIFindCollector(
            session,
            service.app,
            {**service.mid_cfg["ifind"], "max_external_calls": 40},
            authorized_call_limit=40,
        )
        if not collector.gate()["passed"]:
            raise RuntimeError("IFIND_AUTH_FAILED")
        collector._ensure_provider()
        cutoff = datetime.combine(args.trade_date, args.cutoff_time).strftime("%Y-%m-%d %H:%M:%S")
        if args.format_probe:
            results = []
            for canonical in args.format_probe:
                code = normalize_ts_code(canonical)
                number, suffix = code.split(".", 1)
                variants = [code, number, f"{suffix}{number}"]
                if suffix == "SH":
                    variants.extend([f"{number}.SS", f"{number}.SHSE"])
                elif suffix == "BJ":
                    variants.extend([f"{number}.BSE", f"BJSE{number}"])
                for variant in list(dict.fromkeys(variants)):
                    try:
                        response = collector.client.post("snap_shot", {
                            "codes": variant,
                            "indicators": "tradeDate,tradeTime,preClose,open,high,low,latest,volume,amount",
                            "starttime": cutoff,
                            "endtime": cutoff,
                        })
                    except IFindHttpError as exc:
                        results.append({"canonical": code, "variant": variant, "error_category": exc.category.value, "row_count": 0, "raw_codes": []})
                        continue
                    rows, structural = normalize_rows_with_audit(response.payload)
                    results.append({
                        "canonical": code,
                        "variant": variant,
                        "errorcode": response.payload.get("errorcode"),
                        "errmsg": response.payload.get("errmsg"),
                        "row_count": len(rows),
                        "raw_codes": structural["raw_security_code_array"],
                        "parser_branch_used": structural["parser_branch_used"],
                    })
            print(json.dumps({"format_probes": results, "provider_calls": collector.client.call_count}, ensure_ascii=False, indent=2))
            return 0
        audits = []
        missing_pool = []
        for market, market_codes in grouped.items():
            cursor = 0
            for size in args.sizes:
                batch = market_codes[cursor : cursor + size]
                cursor += size
                if len(batch) != size:
                    continue
                response = collector.client.post("snap_shot", {
                    "codes": ",".join(batch),
                    "indicators": "tradeDate,tradeTime,preClose,open,high,low,latest,volume,amount",
                    "starttime": cutoff,
                    "endtime": cutoff,
                })
                rows, structural = normalize_rows_with_audit(response.payload)
                raw = set(structural["raw_security_code_array"])
                parsed = []
                valid = []
                for row in rows:
                    value = row.get("thscode") or row.get("code") or row.get("ts_code")
                    if not value:
                        continue
                    code = normalize_ts_code(value)
                    parsed.append(code)
                    if all(row.get(field) is not None for field in FIELDS) and (row.get("time") or row.get("tradeTime")):
                        valid.append(code)
                requested = set(batch)
                missing_pool.extend(sorted(requested - set(valid)))
                audits.append({
                    "market": market,
                    "requested_codes": batch,
                    "requested_code_count": len(batch),
                    "raw_response_code_count": len(raw),
                    "raw_security_code_array": structural["raw_security_code_array"],
                    "raw_field_array_lengths": structural["raw_field_array_lengths"],
                    "raw_table_count": structural["raw_table_count"],
                    "response_shape": structural["response_shape"],
                    "parser_branch_used": structural["parser_branch_used"],
                    "parser_output_code_count": len(set(parsed)),
                    "normalized_output_code_count": len(set(valid)),
                    "provider_missing_codes": sorted(requested - raw),
                    "parser_missing_codes": sorted((requested & raw) - set(parsed)),
                    "field_invalid_codes": sorted((requested & set(parsed)) - set(valid)),
                    "duplicate_codes": sorted(code for code in set(parsed) if parsed.count(code) > 1),
                    "unexpected_codes": sorted(set(parsed) - requested),
                })
        rng = random.Random(20260720)
        sample = rng.sample(sorted(set(missing_pool)), min(args.single_sample, len(set(missing_pool))))
        single_results = []
        for code in sample:
            response = collector.client.post("snap_shot", {
                "codes": code,
                "indicators": "tradeDate,tradeTime,preClose,open,high,low,latest,volume,amount",
                "starttime": cutoff,
                "endtime": cutoff,
            })
            rows, structural = normalize_rows_with_audit(response.payload)
            valid = any(
                (row.get("thscode") or row.get("code") or row.get("ts_code"))
                and all(row.get(field) is not None for field in FIELDS)
                and (row.get("time") or row.get("tradeTime"))
                for row in rows
            )
            single_results.append({
                "stock_code": code,
                "success": valid,
                "row_count": len(rows),
                "parser_branch_used": structural["parser_branch_used"],
                "response_shape": structural["response_shape"],
            })
        report = {
            "trade_date": args.trade_date.isoformat(),
            "cutoff": cutoff,
            "audits": audits,
            "single_code_sample": single_results,
            "provider_calls": collector.client.call_count,
            "successful_calls": collector.client.success_count,
            "failed_calls": collector.client.failed_count,
            "contains_sensitive_headers": False,
        }
        target = output_root() / args.trade_date.isoformat() / "午盘推荐_全A" / f"snapshot_parser_diagnosis_{uuid.uuid4().hex[:8]}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({
            "output": str(target),
            "batches": [{k: row[k] for k in ("market", "requested_code_count", "raw_response_code_count", "parser_output_code_count", "normalized_output_code_count")} for row in audits],
            "single_code_successes": sum(row["success"] for row in single_results),
            "single_code_sample": len(single_results),
            "provider_calls": collector.client.call_count,
        }, ensure_ascii=False, indent=2))
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
