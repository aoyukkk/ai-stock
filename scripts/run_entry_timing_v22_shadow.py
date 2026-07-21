from __future__ import annotations
import argparse,json,sys
from datetime import date
from pathlib import Path
from dotenv import load_dotenv
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from backend.core.runtime_paths import tushare_cache_root
from database.session import get_session,init_db
from entry_timing.historical_v22 import HistoricalV22Validator

def main():
    parser=argparse.ArgumentParser(description="运行V2.2市场状态、集中度、盘中触发与相对绩效Shadow回放")
    parser.add_argument("--historical-start",type=date.fromisoformat,required=True);parser.add_argument("--historical-end",type=date.fromisoformat,required=True);args=parser.parse_args();load_dotenv(ROOT/".env",override=False);init_db();session=get_session()
    try:
        output=ROOT/"outputs"/args.historical_end.isoformat()/"复盘"/f"买入准入V2_2历史验证_{args.historical_start}_至_{args.historical_end}.xlsx"
        result=HistoricalV22Validator(session,cache_root=tushare_cache_root()).run(args.historical_start,args.historical_end,output);print(json.dumps(result,ensure_ascii=False,indent=2,default=str))
    finally:session.close()
    return 0
if __name__=="__main__":raise SystemExit(main())
