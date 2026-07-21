from __future__ import annotations

import argparse
import json
import sys
from datetime import date, time
from pathlib import Path

from dotenv import load_dotenv

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

from backend.core.runtime_paths import output_root
from database.session import get_session, init_db
from midday.full_a_service import FINAL_STATUSES, FullAMiddayService
from scripts.run_daily_routine import _assert_advisory_only


def main()->int:
    parser=argparse.ArgumentParser(description="Full-A 11:30 radar plus V2.2 one-shot shadow pipeline")
    parser.add_argument("--trade-date",type=date.fromisoformat,required=True);parser.add_argument("--cutoff-time",type=time.fromisoformat,required=True)
    parser.add_argument("--mode",choices=["FULL_A_MIDDAY_RADAR_V2_2"],default="FULL_A_MIDDAY_RADAR_V2_2")
    parser.add_argument("--real-provider",action="store_true");parser.add_argument("--real-llm",action="store_true");parser.add_argument("--no-orders",action="store_true");parser.add_argument("--no-scheduler",action="store_true");parser.add_argument("--no-production-change",action="store_true");args=parser.parse_args()
    load_dotenv(ROOT/".env",override=False);_assert_advisory_only()
    if not all((args.real_provider,args.real_llm,args.no_orders,args.no_scheduler,args.no_production_change)):raise SystemExit("FULL_A_EXPLICIT_REAL_SHADOW_FLAGS_REQUIRED")
    init_db();session=get_session()
    try:
        result=FullAMiddayService(session,output_root=output_root()).run(args.trade_date,args.cutoff_time);print(json.dumps(result,ensure_ascii=False,indent=2,default=str));return 0 if result.get("status") in FINAL_STATUSES-{"FULL_A_INTEGRATION_FAILED","FULL_A_DATA_CAPACITY_FAILED","FULL_A_DATA_FRESHNESS_FAILED","FULL_A_REGIME_TRANSITION_VIOLATION"} else 2
    finally:session.close()


if __name__=="__main__":raise SystemExit(main())
