from __future__ import annotations

from datetime import date, datetime, timedelta

from datasource.interfaces.market_data import MarketDataProvider
from datasource.models.market import (
    CapitalFlowData,
    FinanceData,
    KLineBar,
    LimitPriceData,
    MarketEmotionData,
    MarketStockInfo,
    PreMarketAuctionData,
    RealtimeQuote,
)


MOCK_TRADE_DATE = date(2026, 1, 5)
MOCK_TIME = "2026-01-05T10:00:00+08:00"


class MockMarketDataProvider(MarketDataProvider):
    def __init__(self) -> None:
        self._stocks = [
            MarketStockInfo(code="000001", name="Ping An Bank", market="SZ", industry="Bank", status="NORMAL"),
            MarketStockInfo(code="000002", name="Vanke A", market="SZ", industry="Real Estate", status="NORMAL"),
            MarketStockInfo(code="000063", name="ZTE", market="SZ", industry="Telecom Equipment", status="NORMAL"),
            MarketStockInfo(code="000333", name="Midea Group", market="SZ", industry="Home Appliance", status="NORMAL"),
            MarketStockInfo(code="000651", name="Gree Electric", market="SZ", industry="Home Appliance", status="NORMAL"),
            MarketStockInfo(code="000858", name="Wuliangye", market="SZ", industry="Baijiu", status="NORMAL"),
            MarketStockInfo(code="002230", name="iFlytek", market="SZ", industry="AI", status="NORMAL"),
            MarketStockInfo(code="300059", name="East Money", market="SZ", industry="Brokerage", status="NORMAL"),
            MarketStockInfo(code="300750", name="CATL", market="SZ", industry="Battery", status="NORMAL"),
            MarketStockInfo(code="600000", name="SPDB", market="SH", industry="Bank", status="NORMAL"),
            MarketStockInfo(code="600036", name="CMB", market="SH", industry="Bank", status="NORMAL"),
            MarketStockInfo(code="600519", name="Kweichow Moutai", market="SH", industry="Baijiu", status="NORMAL"),
        ]

    def get_stock_list(self) -> list[MarketStockInfo]:
        return list(self._stocks)

    def get_realtime(self, stock_code: str) -> RealtimeQuote:
        index = self._index(stock_code)
        pre_close = round(10 + index * 0.87, 2)
        price = round(pre_close * (1 + ((index % 7) - 3) / 100), 2)
        return RealtimeQuote(
            stock_code=stock_code,
            price=price,
            open=round(pre_close * 1.002, 2),
            high=round(price + 0.18, 2),
            low=round(price - 0.16, 2),
            pre_close=pre_close,
            volume=1_000_000 + index * 45_000,
            amount=round(price * (1_000_000 + index * 45_000), 2),
            change_percent=round((price - pre_close) / pre_close * 100, 2),
            datetime=MOCK_TIME,
        )

    def get_kline(
        self,
        stock_code: str,
        start_date: str | None = None,
        end_date: str | None = None,
        frequency: str = "daily",
    ) -> list[KLineBar]:
        end = _parse_date(end_date) if end_date else MOCK_TRADE_DATE
        start = _parse_date(start_date) if start_date else end - timedelta(days=29)
        bars: list[KLineBar] = []
        current = start
        index = self._index(stock_code)
        base = 10 + index * 0.87
        while current <= end:
            offset = (current - start).days
            open_price = round(base + offset * 0.03, 2)
            close = round(open_price + ((offset % 5) - 2) * 0.04, 2)
            pre_close = round(open_price - 0.02, 2)
            bars.append(
                KLineBar(
                    stock_code=stock_code,
                    datetime=current.isoformat(),
                    open=open_price,
                    high=round(max(open_price, close) + 0.15, 2),
                    low=round(min(open_price, close) - 0.12, 2),
                    close=close,
                    pre_close=pre_close,
                    volume=900_000 + offset * 8_000,
                    amount=round(close * (900_000 + offset * 8_000), 2),
                    turnover_rate=round(1.2 + offset % 5 * 0.1, 2),
                    change_percent=round((close - pre_close) / pre_close * 100, 2),
                )
            )
            current += timedelta(days=1)
        return bars

    def get_finance(self, stock_code: str) -> FinanceData:
        index = self._index(stock_code)
        return FinanceData(
            stock_code=stock_code,
            revenue=1_000_000_000 + index * 12_000_000,
            profit=120_000_000 + index * 1_500_000,
            pe=18.5 + index * 0.2,
            pb=2.1,
            roe=12.5,
            debt_ratio=35.0,
        )

    def get_capital_flow(self, stock_code: str) -> CapitalFlowData:
        index = self._index(stock_code)
        return CapitalFlowData(
            stock_code=stock_code,
            main_net_inflow=5_000_000 + index * 100_000,
            large_order_net_inflow=2_000_000 + index * 80_000,
            amount=120_000_000 + index * 1_000_000,
            turnover_rate=2.5 + index % 4 * 0.1,
        )

    def get_market_emotion(self) -> MarketEmotionData:
        return MarketEmotionData(
            limit_up_count=45,
            limit_down_count=6,
            consecutive_limit_up_height=5,
            break_board_rate=18.5,
            hot_industries=["AI", "Battery", "Brokerage"],
        )

    def get_limit_price(self, stock_code: str) -> LimitPriceData:
        quote = self.get_realtime(stock_code)
        return LimitPriceData(
            stock_code=stock_code,
            limit_up_price=round(quote.pre_close * 1.1, 2),
            limit_down_price=round(quote.pre_close * 0.9, 2),
            trade_date=MOCK_TRADE_DATE.isoformat(),
        )

    def get_pre_market_auction(self, stock_code: str) -> PreMarketAuctionData:
        quote = self.get_realtime(stock_code)
        auction_price = round(quote.pre_close * 1.015, 2)
        return PreMarketAuctionData(
            stock_code=stock_code,
            trade_date=MOCK_TRADE_DATE.isoformat(),
            auction_price=auction_price,
            auction_volume=120_000,
            auction_amount=round(auction_price * 120_000, 2),
            auction_change_percent=1.5,
            auction_strength_score=66.0,
        )

    def _index(self, stock_code: str) -> int:
        for index, stock in enumerate(self._stocks):
            if stock.code == stock_code:
                return index
        return 0


def _parse_date(value: str) -> date:
    return datetime.strptime(value.replace("-", ""), "%Y%m%d").date()
