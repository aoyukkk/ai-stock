from __future__ import annotations


def normalize_ts_code(value: str) -> str:
    """Return the canonical Tushare code for an A-share identifier."""
    text = str(value or "").strip().upper()
    if not text:
        raise ValueError("STOCK_CODE_REQUIRED")
    if "." in text:
        code, suffix = text.split(".", 1)
        code = code.zfill(6)
        if suffix not in {"SZ", "SH", "BJ"}:
            raise ValueError(f"UNSUPPORTED_STOCK_EXCHANGE:{suffix}")
        return f"{code}.{suffix}"
    code = text.zfill(6)
    if not code.isdigit() or len(code) != 6:
        raise ValueError(f"INVALID_STOCK_CODE:{value}")
    if code.startswith(("4", "8", "920")):
        return f"{code}.BJ"
    if code.startswith("6"):
        return f"{code}.SH"
    return f"{code}.SZ"


def display_stock_code(value: str) -> str:
    return normalize_ts_code(value).split(".", 1)[0]
