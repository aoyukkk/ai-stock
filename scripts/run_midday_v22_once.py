from __future__ import annotations
import argparse,json,sys
from datetime import date,time
from pathlib import Path
from dotenv import load_dotenv
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from backend.core.runtime_paths import output_root
from database.session import get_session,init_db
from midday.v22_service import MiddayV22OneShotService
from scripts.run_daily_routine import _assert_advisory_only

def main():
    parser=argparse.ArgumentParser(description="V2.2 as-of cutoff midday one-shot shadow runner")
    parser.add_argument("--trade-date",type=date.fromisoformat,required=True);parser.add_argument("--cutoff-time",type=time.fromisoformat,required=True)
    parser.add_argument("--mode",choices=["LIVE_SHADOW_ONE_SHOT"],default="LIVE_SHADOW_ONE_SHOT");parser.add_argument("--no-orders",action="store_true");parser.add_argument("--no-scheduler",action="store_true");parser.add_argument("--no-production-change",action="store_true");args=parser.parse_args()
    load_dotenv(ROOT/".env",override=False);_assert_advisory_only()
    if not all((args.no_orders,args.no_scheduler,args.no_production_change)):raise SystemExit("V22_EXPLICIT_SAFETY_FLAGS_REQUIRED")
    init_db();session=get_session()
    try:result=MiddayV22OneShotService(session,output_root=output_root()).run(args.trade_date,args.cutoff_time);print(json.dumps(result,ensure_ascii=False,indent=2,default=str));return 0 if result.get("status") in {"SUCCESS","EMPTY_POOL","PARTIAL"} else 2
    finally:session.close()
if __name__=="__main__":raise SystemExit(main())
