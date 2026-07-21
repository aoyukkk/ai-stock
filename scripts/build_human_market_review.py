from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.session import get_session, init_db
from market_review.human_excel import HumanMarketReviewExcelExporter
from market_review.service import MarketReviewService


def main() -> int:
    parser = argparse.ArgumentParser(description="生成中文大盘复盘工作簿和简明文本。")
    parser.add_argument("--trade-date", required=True, type=date.fromisoformat)
    parser.add_argument("--run-id")
    parser.add_argument("--supplement", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    load_dotenv(ROOT / ".env", override=False)
    init_db()
    session = get_session()
    try:
        bundle = MarketReviewService(session).latest(args.trade_date, args.run_id)
    finally:
        session.close()
    if not bundle:
        raise ValueError("MARKET_REVIEW_RUN_NOT_FOUND")
    supplement = json.loads(args.supplement.read_text(encoding="utf-8")) if args.supplement else {}
    output = args.output or ROOT / "outputs" / args.trade_date.isoformat() / "复盘" / f"大盘复盘_{args.trade_date.isoformat()}.xlsx"
    result = HumanMarketReviewExcelExporter().export(output.resolve(), bundle, supplement)
    markdown = Path(result["output"]).with_suffix(".md")
    markdown.write_text(_markdown(bundle, supplement), encoding="utf-8")
    result["markdown"] = str(markdown)
    result["market_review_run_id"] = bundle["run"]["run_id"]
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def _markdown(bundle: dict, supplement: dict) -> str:
    snapshot = bundle.get("snapshot") or {}
    breadth = snapshot.get("breadth") or {}
    turnover = snapshot.get("turnover") or {}
    limits = snapshot.get("limit_structure") or {}
    advancing = int(breadth.get("advancing_count") or 0)
    declining = int(breadth.get("declining_count") or 0)
    equal_weight = float(breadth.get("equal_weight_return") or 0)
    if advancing > declining and equal_weight >= 0:
        breadth_summary = "全A个股整体偏强"
    elif declining > advancing and equal_weight <= 0:
        breadth_summary = "全A个股整体偏弱"
    else:
        breadth_summary = "全A个股涨跌分化"
    lines = [
        f"# {bundle['run']['trade_date']} A股大盘复盘",
        "",
        "## 今日走势",
        "",
        (
            f"{breadth_summary}。全A样本中上涨 {breadth.get('advancing_count')} 家、"
            f"下跌 {breadth.get('declining_count')} 家、平盘 {breadth.get('flat_count')} 家；"
            f"全A等权涨跌 {float(breadth.get('equal_weight_return') or 0):+.2%}。"
        ),
        (
            f"两市及北交所成交额约 {float(turnover.get('total_amount') or 0) / 100_000_000:.2f} 亿元，"
            f"较上一交易日 {float(turnover.get('change_ratio') or 0):+.2%}。"
            f"涨停 {limits.get('limit_up_count')} 家、跌停 {limits.get('limit_down_count')} 家。"
        ),
        "",
        "## 主要原因",
        "",
    ]
    for item in supplement.get("causes") or []:
        lines.append(f"- **{item.get('direction')}**：{item.get('conclusion')} [{item.get('source_name')}]({item.get('source_url')})")
    if not supplement.get("causes"):
        for item in bundle.get("drivers") or []:
            lines.append(f"- **{item.get('title')}**：{item.get('explanation')}")
        industries = list(snapshot.get("industries") or [])
        if industries:
            leaders = "、".join(str(item.get("sector_name")) for item in industries[:3])
            laggards = "、".join(str(item.get("sector_name")) for item in industries[-3:])
            lines.append(f"- **行业轮动**：领涨为 {leaders}；偏弱为 {laggards}。")
    lines.extend([
        "",
        "## 明日观察",
        "",
        f"- 基准情景 {bundle['run'].get('base_case_probability')}%：继续轮动分化。",
        f"- 偏强情景 {bundle['run'].get('bull_case_probability')}%：量能回升且科技止跌。",
        f"- 偏弱情景 {bundle['run'].get('bear_case_probability')}%：缩量下探或领涨方向回落。",
        "- 优先看成交额、半导体止跌情况、医药白酒持续性和炸板率。",
        "",
    ])
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
