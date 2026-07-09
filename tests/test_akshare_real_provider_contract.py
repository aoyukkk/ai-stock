import sys
import types

from datasource.akshare_provider import AKShareMarketDataProvider


class FakeFrame:
    def __init__(self, records: list[dict]) -> None:
        self.records = records

    def to_dict(self, orient: str = "records") -> list[dict]:
        assert orient == "records"
        return self.records


def test_akshare_contract_uses_mocked_library_without_network(monkeypatch, tmp_path) -> None:
    fake = types.SimpleNamespace(
        stock_zh_a_spot_em=lambda: FakeFrame([
            {
                "\u4ee3\u7801": "000001",
                "\u540d\u79f0": "Ping An Bank",
                "\u6700\u65b0\u4ef7": 10.5,
                "\u6628\u6536": 10,
                "\u6210\u4ea4\u91cf": 1000,
                "\u6210\u4ea4\u989d": 10500,
            }
        ]),
        stock_zh_a_hist=lambda **kwargs: FakeFrame([
            {
                "\u65e5\u671f": "2025-01-01",
                "\u5f00\u76d8": 10,
                "\u6700\u9ad8": 11,
                "\u6700\u4f4e": 9,
                "\u6536\u76d8": 10.5,
                "\u6628\u6536": 10,
                "\u6210\u4ea4\u91cf": 1000,
                "\u6210\u4ea4\u989d": 10500,
            }
        ]),
    )
    monkeypatch.setitem(sys.modules, "akshare", fake)

    provider = AKShareMarketDataProvider(cache_dir=tmp_path)

    assert provider.get_stock_list()[0].source == "akshare"
    assert provider.get_realtime("000001").stock_code == "000001"
    assert provider.get_kline("000001", "2025-01-01", "2025-01-01")[0].source == "akshare"
    assert provider.get_limit_price("000001").source_status == "debug_simplified"
