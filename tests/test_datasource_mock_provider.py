from datetime import date

from datasource.mock_provider import MockDataProvider


def test_mock_stock_list_returns_at_least_twenty_a_share_stocks() -> None:
    provider = MockDataProvider()
    stocks = provider.get_stock_list()

    assert len(stocks) >= 20
    assert all(stock.stock_code for stock in stocks)
    assert all(stock.market in {"SH", "SZ"} for stock in stocks)


def test_mock_realtime_quotes_returns_requested_stocks() -> None:
    provider = MockDataProvider()
    quotes = provider.get_realtime_quotes(["000001", "600519"])

    assert [quote.stock_code for quote in quotes] == ["000001", "600519"]
    assert all(quote.current_price > 0 for quote in quotes)
    assert all(quote.volume > 0 for quote in quotes)


def test_mock_kline_returns_ohlcv() -> None:
    provider = MockDataProvider()
    bars = provider.get_kline("000001", date(2026, 1, 1), date(2026, 1, 5))

    assert len(bars) == 5
    assert all(bar.stock_code == "000001" for bar in bars)
    assert all(bar.high >= bar.low for bar in bars)
    assert all(bar.volume > 0 for bar in bars)


def test_mock_news_and_overseas_data_are_available() -> None:
    provider = MockDataProvider()

    news = provider.get_latest_news(limit=2)
    indices = provider.get_overseas_indices()

    assert len(news) == 2
    assert all(item.source == "mock_news" for item in news)
    assert indices
    assert all(index.category == "index" for index in indices)
