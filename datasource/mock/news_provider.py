from __future__ import annotations

from datasource.interfaces.news_data import NewsDataProvider
from datasource.models.news import NewsItem


class MockNewsDataProvider(NewsDataProvider):
    def __init__(self) -> None:
        self._news = [
            NewsItem(
                title="Policy support for advanced manufacturing",
                content="Policy update supports high quality manufacturing and AI adoption.",
                source="mock_policy",
                publish_time="2026-01-05T08:30:00+08:00",
                importance=86,
                sentiment="POSITIVE",
                url="https://example.invalid/policy",
                stock_code="300750",
            ),
            NewsItem(
                title="Consumer sector demand pressure",
                content="Mock negative news used to verify risk warnings.",
                source="mock_news",
                publish_time="2026-01-05T09:00:00+08:00",
                importance=72,
                sentiment="NEGATIVE",
                url="https://example.invalid/consumer-risk",
                stock_code="600519",
            ),
            NewsItem(
                title="Brokerage activity improves",
                content="Trading activity improves in the mock market.",
                source="mock_news",
                publish_time="2026-01-05T09:20:00+08:00",
                importance=68,
                sentiment="POSITIVE",
                url="https://example.invalid/brokerage",
                stock_code="300059",
            ),
            NewsItem(
                title="Bank sector remains stable",
                content="Large banks remain stable in deterministic mock data.",
                source="mock_news",
                publish_time="2026-01-05T09:35:00+08:00",
                importance=55,
                sentiment="NEUTRAL",
                url="https://example.invalid/bank",
                stock_code="000001",
            ),
            NewsItem(
                title="Overseas technology sentiment mixed",
                content="Overseas market signal is mixed for local technology names.",
                source="mock_overseas",
                publish_time="2026-01-05T09:45:00+08:00",
                importance=61,
                sentiment="NEUTRAL",
                url="https://example.invalid/overseas",
                stock_code="000063",
            ),
        ]

    def get_latest_news(self) -> list[NewsItem]:
        return list(self._news)

    def get_stock_news(self, stock_code: str) -> list[NewsItem]:
        return [item for item in self._news if item.stock_code == stock_code]

    def get_announcements(self, stock_code: str) -> list[NewsItem]:
        return [
            NewsItem(
                title=f"Mock announcement for {stock_code}",
                content="Mock announcement for manual debug mode.",
                source="mock_announcement",
                publish_time="2026-01-05T07:45:00+08:00",
                importance=50,
                sentiment="NEUTRAL",
                url="https://example.invalid/announcement",
                stock_code=stock_code,
            )
        ]

    def get_policy_news(self) -> list[NewsItem]:
        return [item for item in self._news if item.source == "mock_policy"]

    def search_news(self, keyword: str) -> list[NewsItem]:
        lowered = keyword.lower()
        return [
            item
            for item in self._news
            if lowered in item.title.lower() or lowered in item.content.lower()
        ]
