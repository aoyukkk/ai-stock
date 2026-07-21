from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

from backend.core.runtime_paths import tushare_cache_root
from database.session import get_session,init_db
from entry_timing.historical_v2 import HistoricalEntryTimingV2Validator
from entry_timing.service_v2 import EntryTimingV2ShadowService


def main():
    parser=argparse.ArgumentParser(description="运行策略感知买入准入V2.1影子分析，零LLM、零外部API、零订单。")
    parser.add_argument("--trade-date",type=date.fromisoformat)
    parser.add_argument("--historical-start",type=date.fromisoformat)
    parser.add_argument("--historical-end",type=date.fromisoformat)
    parser.add_argument("--quant-run-id")
    args=parser.parse_args();load_dotenv(ROOT/".env",override=False);init_db();session=get_session()
    try:
        if args.historical_start and args.historical_end:
            output=ROOT/"outputs"/args.historical_end.isoformat()/"复盘"/f"买入准入V2_1历史验证_{args.historical_start.isoformat()}_至_{args.historical_end.isoformat()}.xlsx"
            result=HistoricalEntryTimingV2Validator(session,cache_root=tushare_cache_root()).run(args.historical_start,args.historical_end,output)
        elif args.trade_date:
            result=EntryTimingV2ShadowService(session).run(args.trade_date,quant_run_id=args.quant_run_id,candidate_mode="QUANT_TOP100",force_shadow=True)
        else:raise ValueError("TRADE_DATE_OR_HISTORICAL_RANGE_REQUIRED")
        print(json.dumps(result,ensure_ascii=False,indent=2,default=str))
    finally:session.close()
    return 0


if __name__=="__main__":raise SystemExit(main())
