from __future__ import annotations

from pydantic import BaseModel


class NewsItem(BaseModel):
    title: str
    content: str
    source: str
    publish_time: str
    importance: float
    sentiment: str
    url: str = ""
    stock_code: str | None = None
