from datasource.placeholder_provider import PlaceholderMarketProvider


class THSProvider(PlaceholderMarketProvider):
    def __init__(self) -> None:
        super().__init__(name="ths")
