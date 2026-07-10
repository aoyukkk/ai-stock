from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Iterator

from stock_codes import normalize_ts_code


@dataclass(frozen=True)
class BoundaryViolation:
    category: str
    path: str
    rule: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


_NEGATION = re.compile(r"(?:不|未|无|没有|缺少|不得|无法|不能|不足以|难以|尚未|并非|不是|未能|UNKNOWN|INSUFFICIENT)", re.I)
_URL = re.compile(r"https?://|www\.", re.I)
_LEADERSHIP = re.compile(r"(?:全球|国内|行业|市场)(?:绝对)?(?:第一|领先|龙头)|市占率(?:第一|领先)")
_CURRENT = re.compile(r"(?:今日|目前|最新|近期消息|刚刚|实时|现阶段).{0,20}(?:显示|表明|确认|发生|上涨|下跌|发布|宣布|获得)")
_NETWORK = re.compile(r"(?:已经|已|通过)(?:联网|网络|互联网)(?:查询|检索|搜索)|联网查询(?:显示|发现)")
_MARKET_SHARE = re.compile(r"(?:市场份额|市占率)(?:为|达到|约为|超过)?\s*\d+(?:\.\d+)?%")
_CUSTOMER = re.compile(r"(?:客户包括|主要客户(?:为|是)|客户名称[:：])\s*[\u4e00-\u9fa5A-Za-z]{2,}")
_ORDER = re.compile(r"(?:订单金额|获得订单|签订订单).{0,12}\d+(?:\.\d+)?(?:万|亿|元)")


def scan_output_boundary(payload: dict[str, Any], *, expected_stock_code: str) -> BoundaryViolation | None:
    output_code = str(payload.get("stock_code") or "")
    try:
        mismatch = normalize_ts_code(output_code) != normalize_ts_code(expected_stock_code)
    except ValueError:
        mismatch = True
    if mismatch:
        return BoundaryViolation("STOCK_CODE_MISMATCH", "$.stock_code", "stock_code_exact_match", "输出股票代码与输入不一致")

    for path, value in _walk_strings(payload):
        if _URL.search(value):
            return BoundaryViolation("FABRICATED_URL", path, "no_urls", "结构化输入模式不得输出 URL")
        if _has_positive_match(_NETWORK, value):
            return BoundaryViolation("OUTPUT_BOUNDARY_VIOLATION", path, "no_network_claim", "不得声称已经联网查询")
        if _has_positive_match(_CURRENT, value):
            return BoundaryViolation("FORBIDDEN_CURRENT_INFORMATION", path, "no_current_information", "输出包含未经输入支持的时效性陈述")
        if _has_positive_match(_MARKET_SHARE, value):
            return BoundaryViolation("UNSUPPORTED_CLAIM", path, "no_unsupported_market_share", "输出包含未经输入支持的具体市场份额")
        if _has_positive_match(_CUSTOMER, value):
            return BoundaryViolation("UNSUPPORTED_CLAIM", path, "no_unsupported_customer", "输出包含未经输入支持的具体客户")
        if _has_positive_match(_ORDER, value):
            return BoundaryViolation("UNSUPPORTED_CLAIM", path, "no_unsupported_order", "输出包含未经输入支持的具体订单金额")
        if _has_positive_match(_LEADERSHIP, value):
            return BoundaryViolation("UNSUPPORTED_CLAIM", path, "no_unsupported_leadership_claim", "输出包含未经输入支持的领先或排名陈述")
    return None


def _has_positive_match(pattern: re.Pattern[str], text: str) -> bool:
    for match in pattern.finditer(text):
        prefix = text[max(0, match.start() - 14):match.start()]
        if not _NEGATION.search(prefix):
            return True
    return False


def _walk_strings(value: Any, path: str = "$") -> Iterator[tuple[str, str]]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk_strings(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_strings(item, f"{path}[{index}]")
    elif isinstance(value, str):
        yield path, value
