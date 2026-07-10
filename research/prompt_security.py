from __future__ import annotations

import html
import re


INJECTION_PATTERNS = (
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"system\s+prompt",
    r"developer\s+message",
    r"reveal\s+.*(?:secret|api\s*key|credential)",
    r"执行.*(?:系统|开发者).*(?:指令|提示)",
)


def sanitize_untrusted_web_text(value: str, max_chars: int = 12000) -> tuple[str, list[str]]:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    warnings = []
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            warnings.append("PROMPT_INJECTION_PATTERN_IGNORED")
            text = re.sub(pattern, "[UNTRUSTED_INSTRUCTION_REMOVED]", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()[:max_chars]
    return f"<UNTRUSTED_WEB_CONTENT>{text}</UNTRUSTED_WEB_CONTENT>", sorted(set(warnings))
