from datasource.placeholder_provider import PlaceholderNewsProvider


class RealNewsProvider(PlaceholderNewsProvider):
    def __init__(self, name: str = "real_news") -> None:
        super().__init__(name=name)
