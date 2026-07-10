QUERY_TEMPLATES = {
    "A": "{stock_name} {stock_code} 主营业务 产业链 年报",
    "B": "{stock_name} 行业地位 竞争优势 市场份额",
    "C": "{stock_name} 国产替代 核心技术 风险",
    "D": "{stock_name} 财务状况 营收 利润 现金流 公告",
}


def render_query(template_id: str, *, stock_code: str, stock_name: str) -> str:
    return QUERY_TEMPLATES[template_id].format(stock_code=stock_code, stock_name=stock_name)
