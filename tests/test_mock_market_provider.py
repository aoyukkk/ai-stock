from datasource.mock.market_provider import MockMarketDataProvider
from datasource.models.market import (
    CapitalFlowData,
    FinanceData,
    LimitPriceData,
    MarketEmotionData,
    PreMarketAuctionData,
    RealtimeQuote,
)


def test_mock_market_provider_returns_stock_list() -> None:
    provider = MockMarketDataProvider()
    stocks = provider.get_stock_list()

    assert len(stocks) >= 10
    assert stocks[0].code == "000001"


def test_mock_market_provider_returns_realtime_quote() -> None:
    assert isinstance(MockMarketDataProvider().get_realtime("000001"), RealtimeQuote)


def test_mock_market_provider_returns_kline_bars() -> None:
    bars = MockMarketDataProvider().get_kline("000001")

    assert len(bars) >= 30
    assert bars[0].stock_code == "000001"


def test_mock_market_provider_returns_finance_and_flow() -> None:
    provider = MockMarketDataProvider()

    assert isinstance(provider.get_finance("000001"), FinanceData)
    assert isinstance(provider.get_capital_flow("000001"), CapitalFlowData)


def test_mock_market_provider_returns_emotion_limit_and_auction() -> None:
    provider = MockMarketDataProvider()

    assert isinstance(provider.get_market_emotion(), MarketEmotionData)
    assert isinstance(provider.get_limit_price("000001"), LimitPriceData)
    assert isinstance(provider.get_pre_market_auction("000001"), PreMarketAuctionData)
