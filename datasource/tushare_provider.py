from datasource.placeholder_provider import PlaceholderMarketProvider


class TushareProvider(PlaceholderMarketProvider):
    def __init__(self) -> None:
        super().__init__(name="tushare")
