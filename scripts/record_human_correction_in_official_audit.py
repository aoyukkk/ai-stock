from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", type=Path, nargs="+", required=True)
    parser.add_argument("--workbook", type=Path, required=True)
    parser.add_argument("--correction", type=Path, required=True)
    args = parser.parse_args()
    workbook_hash = hashlib.sha256(args.workbook.read_bytes()).hexdigest()
    correction = json.loads(args.correction.read_text(encoding="utf-8"))
    for path in args.audit:
        payload = json.loads(path.read_text(encoding="utf-8"))
        output_paths = payload.setdefault("output_paths", {})
        output_paths["main_workbook_sha256"] = workbook_hash
        payload["human_correction"] = {
            "status": correction.get("status", "HUMAN_COMPLETED"),
            "stock_code": correction.get("stock_code"),
            "stock_name": correction.get("stock_name"),
            "audit_path": str(args.correction.resolve()),
            "quant_ranking_changed": False,
            "flash_score_changed": False,
            "pro_ranking_changed": False,
            "historical_failure_preserved": True,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
