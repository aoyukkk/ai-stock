from datasource.placeholder_provider import PlaceholderOverseasProvider


class RealOverseasProvider(PlaceholderOverseasProvider):
    def __init__(self, name: str = "real_overseas") -> None:
        super().__init__(name=name)
