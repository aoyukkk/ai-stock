from __future__ import annotations

import math

from llm_gateway.schemas import LLMMessage


def estimate_text_tokens(text: str) -> int:
    if not text:
        return 0
    ascii_chars = sum(1 for char in text if ord(char) < 128 and not char.isspace())
    non_ascii_chars = sum(1 for char in text if ord(char) >= 128)
    whitespace_groups = len(text.split())
    rough = math.ceil(ascii_chars / 4) + non_ascii_chars + whitespace_groups
    return max(1, rough)


def estimate_messages_tokens(messages: list[LLMMessage]) -> int:
    return sum(estimate_text_tokens(message.content) + 2 for message in messages)
