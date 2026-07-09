from datasource.mock.news_provider import MockNewsDataProvider


def test_mock_news_provider_returns_latest_news() -> None:
    news = MockNewsDataProvider().get_latest_news()

    assert len(news) >= 5
    assert any(item.sentiment == "POSITIVE" for item in news)
    assert any(item.sentiment == "NEGATIVE" for item in news)


def test_mock_news_provider_filters_stock_news() -> None:
    news = MockNewsDataProvider().get_stock_news("000001")

    assert news
    assert all(item.stock_code == "000001" for item in news)


def test_mock_news_provider_returns_policy_news() -> None:
    news = MockNewsDataProvider().get_policy_news()

    assert news
    assert all(item.source == "mock_policy" for item in news)
