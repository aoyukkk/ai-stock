from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from datasource.base import MarketDataProvider, NewsDataProvider, OverseasDataProvider
from datasource.schemas import (
    CapitalFlowSnapshot,
    FinanceSnapshot,
    KlineBar,
    LimitPriceInfo,
    MarketEmotionSnapshot,
    NewsItem,
    OverseasMarketSnapshot,
    PreMarketAuctionInfo,
    ProviderStatus,
    RealtimeQuote,
    StockInfo,
)


MOCK_TODAY = date(2026, 1, 5)
MOCK_NOW = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)


class MockDataProvider(MarketDataProvider, NewsDataProvider, OverseasDataProvider):
    def __init__(self, enabled: bool = True) -> None:
        super().__init__(
            name="mock",
            provider_type="market_news_overseas",
            enabled=enabled,
            is_mock=True,
        )
        self._stocks = _build_stocks()

    def health_check(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.name,
            provider_type=self.provider_type,
            enabled=self.enabled,
            is_mock=self.is_mock,
            healthy=self.enabled,
            status="ok" if self.enabled else "disabled",
            message="Mock provider uses deterministic local data.",
        )

    def get_stock_list(self) -> list[StockInfo]:
        return list(self._stocks)

    def get_realtime_quotes(self, stock_codes: list[str]) -> list[RealtimeQuote]:
        requested = set(stock_codes) if stock_codes else {stock.stock_code for stock in self._stocks}
        quotes: list[RealtimeQuote] = []
        for index, stock in enumerate(self._stocks):
            if stock.stock_code not in requested:
                continue
            base_price = Decimal("8.00") + Decimal(index) * Decimal("0.37")
            change = Decimal(index % 9 - 4) / Decimal("100")
            current_price = _round_money(base_price * (Decimal("1") + change))
            quotes.append(
                RealtimeQuote(
                    stock_code=stock.stock_code,
                    name=stock.name,
                    current_price=current_price,
                    change_percent=change * Decimal("100"),
                    volume=1_000_000 + index * 25_000,
                    amount=_round_amount(current_price * Decimal(1_000_000 + index * 25_000)),
                    quote_time=MOCK_NOW,
                )
            )
        return quotes

    def get_kline(
        self,
        stock_code: str,
        start_date: date,
        end_date: date,
        frequency: str = "1d",
    ) -> list[KlineBar]:
        bars: list[KlineBar] = []
        current = start_date
        index = self._stock_index(stock_code)
        base_price = Decimal("8.00") + Decimal(index) * Decimal("0.37")

        while current <= end_date:
            day_offset = (current - start_date).days
            open_price = _round_money(base_price + Decimal(day_offset) * Decimal("0.05"))
            close_price = _round_money(open_price + Decimal((day_offset % 5) - 2) * Decimal("0.03"))
            high = max(open_price, close_price) + Decimal("0.12")
            low = min(open_price, close_price) - Decimal("0.10")
            bars.append(
                KlineBar(
                    stock_code=stock_code,
                    trade_date=current,
                    open=open_price,
                    high=_round_money(high),
                    low=_round_money(low),
                    close=close_price,
                    pre_close=_round_money(open_price - Decimal("0.02")),
                    volume=900_000 + index * 10_000 + day_offset * 1_000,
                    amount=_round_amount(close_price * Decimal(900_000 + index * 10_000)),
                    frequency=frequency,
                )
            )
            current += timedelta(days=1)
        return bars

    def get_finance(self, stock_code: str) -> FinanceSnapshot:
        index = self._stock_index(stock_code)
        return FinanceSnapshot(
            stock_code=stock_code,
            date=MOCK_TODAY,
            revenue=Decimal("1000000000") + Decimal(index) * Decimal("10000000"),
            profit=Decimal("120000000") + Decimal(index) * Decimal("1000000"),
            pe=Decimal("18.50") + Decimal(index) / Decimal("10"),
            pb=Decimal("2.10"),
            roe=Decimal("12.50"),
            debt_ratio=Decimal("35.00"),
        )

    def get_capital_flow(self, stock_code: str) -> CapitalFlowSnapshot:
        index = self._stock_index(stock_code)
        return CapitalFlowSnapshot(
            stock_code=stock_code,
            trade_date=MOCK_TODAY,
            main_net_inflow=Decimal("5000000") + Decimal(index) * Decimal("100000"),
            retail_net_inflow=Decimal("-1200000") + Decimal(index) * Decimal("10000"),
            turnover_rate=Decimal("2.50") + Decimal(index % 5) / Decimal("10"),
            volume_ratio=Decimal("1.10") + Decimal(index % 4) / Decimal("10"),
        )

    def get_market_emotion(self) -> MarketEmotionSnapshot:
        return MarketEmotionSnapshot(
            trade_date=MOCK_TODAY,
            limit_up_count=45,
            limit_down_count=6,
            up_count=3200,
            down_count=1650,
            emotion_score=Decimal("68.50"),
        )

    def get_limit_price(self, stock_code: str, trade_date: date) -> LimitPriceInfo:
        index = self._stock_index(stock_code)
        previous_close = Decimal("8.00") + Decimal(index) * Decimal("0.37")
        return LimitPriceInfo(
            stock_code=stock_code,
            trade_date=trade_date,
            previous_close=_round_money(previous_close),
            limit_up_price=_round_money(previous_close * Decimal("1.10")),
            limit_down_price=_round_money(previous_close * Decimal("0.90")),
        )

    def get_pre_market_auction(
        self,
        stock_code: str,
        trade_date: date,
    ) -> PreMarketAuctionInfo:
        limit_price = self.get_limit_price(stock_code, trade_date)
        auction_price = _round_money(limit_price.previous_close * Decimal("1.015"))
        return PreMarketAuctionInfo(
            stock_code=stock_code,
            trade_date=trade_date,
            auction_price=auction_price,
            auction_volume=120_000,
            auction_amount=_round_amount(auction_price * Decimal("120000")),
            auction_change_percent=Decimal("1.50"),
            auction_strength_score=Decimal("66.00"),
        )

    def get_latest_news(self, limit: int = 50) -> list[NewsItem]:
        news = _build_news()
        return news[:limit]

    def search_news(
        self,
        keyword: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[NewsItem]:
        keyword_lower = keyword.lower()
        results = [
            item
            for item in _build_news()
            if keyword_lower in item.title.lower() or keyword_lower in item.content.lower()
        ]
        if start_time:
            results = [item for item in results if item.publish_time >= start_time]
        if end_time:
            results = [item for item in results if item.publish_time <= end_time]
        return results

    def get_overseas_indices(self) -> list[OverseasMarketSnapshot]:
        return [
            OverseasMarketSnapshot(
                name="S&P 500",
                market="US",
                value=Decimal("5100.25"),
                change_percent=Decimal("0.42"),
                snapshot_time=MOCK_NOW,
                category="index",
            ),
            OverseasMarketSnapshot(
                name="Hang Seng Index",
                market="HK",
                value=Decimal("18520.75"),
                change_percent=Decimal("-0.31"),
                snapshot_time=MOCK_NOW,
                category="index",
            ),
        ]

    def get_overseas_leaders(self) -> list[OverseasMarketSnapshot]:
        return [
            OverseasMarketSnapshot(
                name="NVIDIA",
                market="US",
                value=Decimal("915.20"),
                change_percent=Decimal("1.20"),
                snapshot_time=MOCK_NOW,
                category="leader",
            )
        ]

    def get_fx_rates(self) -> list[OverseasMarketSnapshot]:
        return [
            OverseasMarketSnapshot(
                name="USD/CNY",
                market="FX",
                value=Decimal("7.1800"),
                change_percent=Decimal("0.05"),
                snapshot_time=MOCK_NOW,
                category="fx",
            )
        ]

    def get_commodities(self) -> list[OverseasMarketSnapshot]:
        return [
            OverseasMarketSnapshot(
                name="Brent Oil",
                market="COMMODITY",
                value=Decimal("82.10"),
                change_percent=Decimal("-0.20"),
                snapshot_time=MOCK_NOW,
                category="commodity",
            )
        ]

    def get_crypto_market(self) -> list[OverseasMarketSnapshot]:
        return [
            OverseasMarketSnapshot(
                name="BTC",
                market="CRYPTO",
                value=Decimal("68000.00"),
                change_percent=Decimal("0.80"),
                snapshot_time=MOCK_NOW,
                category="crypto",
            )
        ]

    def _stock_index(self, stock_code: str) -> int:
        for index, stock in enumerate(self._stocks):
            if stock.stock_code == stock_code:
                return index
        return 0


def _build_stocks() -> list[StockInfo]:
    names = [
        ("000001", "平安银行", "SZ", "银行"),
        ("000002", "万科A", "SZ", "房地产"),
        ("000063", "中兴通讯", "SZ", "通信设备"),
        ("000333", "美的集团", "SZ", "家电"),
        ("000651", "格力电器", "SZ", "家电"),
        ("000725", "京东方A", "SZ", "半导体显示"),
        ("000858", "五粮液", "SZ", "白酒"),
        ("002027", "分众传媒", "SZ", "传媒"),
        ("002230", "科大讯飞", "SZ", "人工智能"),
        ("002415", "海康威视", "SZ", "安防"),
        ("002594", "比亚迪", "SZ", "新能源汽车"),
        ("300014", "亿纬锂能", "SZ", "电池"),
        ("300059", "东方财富", "SZ", "证券"),
        ("300124", "汇川技术", "SZ", "工业自动化"),
        ("300750", "宁德时代", "SZ", "电池"),
        ("600000", "浦发银行", "SH", "银行"),
        ("600036", "招商银行", "SH", "银行"),
        ("600276", "恒瑞医药", "SH", "医药"),
        ("600519", "贵州茅台", "SH", "白酒"),
        ("601318", "中国平安", "SH", "保险"),
        ("601398", "工商银行", "SH", "银行"),
        ("688981", "中芯国际", "SH", "半导体"),
    ]
    return [
        StockInfo(
            stock_code=code,
            name=name,
            market=market,
            industry=industry,
            list_date=date(2010, 1, 1),
            status="NORMAL",
        )
        for code, name, market, industry in names
    ]


def _build_news() -> list[NewsItem]:
    return [
        NewsItem(
            title="Mock policy support for advanced manufacturing",
            content="Local deterministic news for testing provider interfaces.",
            source="mock_news",
            publish_time=MOCK_NOW - timedelta(hours=1),
            importance=Decimal("80.00"),
            sentiment="POSITIVE",
            related_stocks=["300124", "688981"],
        ),
        NewsItem(
            title="Mock overseas technology sector mixed",
            content="Overseas leaders show mixed performance in mock data.",
            source="mock_news",
            publish_time=MOCK_NOW - timedelta(hours=2),
            importance=Decimal("65.00"),
            sentiment="NEUTRAL",
            related_stocks=["002230", "000063"],
        ),
        NewsItem(
            title="Mock consumer sector risk reminder",
            content="Mock risk reminder for short-term trading review.",
            source="mock_news",
            publish_time=MOCK_NOW - timedelta(hours=3),
            importance=Decimal("60.00"),
            sentiment="NEGATIVE",
            related_stocks=["600519", "000858"],
        ),
    ]


def _round_money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def _round_amount(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))
