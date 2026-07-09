from __future__ import annotations

from abc import ABC, abstractmethod

from datasource.models.news import NewsItem


class NewsDataProvider(ABC):
    @abstractmethod
    def get_latest_news(self) -> list[NewsItem]:
        raise NotImplementedError

    @abstractmethod
    def get_stock_news(self, stock_code: str) -> list[NewsItem]:
        raise NotImplementedError

    @abstractmethod
    def get_announcements(self, stock_code: str) -> list[NewsItem]:
        raise NotImplementedError

    @abstractmethod
    def get_policy_news(self) -> list[NewsItem]:
        raise NotImplementedError

    @abstractmethod
    def search_news(self, keyword: str) -> list[NewsItem]:
        raise NotImplementedError
