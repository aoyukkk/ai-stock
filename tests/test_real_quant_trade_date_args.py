import json
import sys
import types

from scripts.run_real_quant_top500 import run_real_quant_top500
from scripts.smoke_real_data_sources import run_smoke_real_data_sources
from scripts import smoke_real_quant_top500


class FakeLoginResult:
    error_code = "0"
    error_msg = ""


class FakeQueryResult:
    def __init__(self, rows: list[list[str]]) -> None:
        self.rows = rows
        self.index = -1
        self.error_code = "0"
        self.error_msg = ""

    def next(self) -> bool:
        self.index += 1
        return self.index < len(self.rows)

    def get_row_data(self) -> list[str]:
        return self.rows[self.index]


def test_run_real_quant_top500_supports_trade_date_args(tmp_path) -> None:
    output = tmp_path / "quant.json"

    report = run_real_quant_top500(
        provider="mock",
        history_provider="mock",
        top_n=5,
        sample_limit=5,
        trade_date="2026-07-08",
        max_lookback_days=20,
        akshare_no_proxy=True,
        output=output,
        progress=False,
    )

    persisted = json.loads(output.read_text(encoding="utf-8"))
    assert report["requested_trade_date"] == "2026-07-08"
    assert persisted["requested_trade_date"] == "2026-07-08"
    assert report["akshare_proxy_mode"] == "no_proxy"
    assert "baostock_date_attempts" in report


def test_smoke_real_quant_wrapper_passes_new_args(monkeypatch) -> None:
    captured = {}

    def fake_run_real_quant_top500(**kwargs):
        captured.update(kwargs)
        return {
            "provider": kwargs["provider"],
            "history_provider": kwargs["history_provider"],
            "universe_count": 0,
            "filtered_count": 0,
            "scored_count": 0,
            "top_count": 0,
            "no_llm_call_verified": True,
            "report_path": kwargs["output"],
        }

    monkeypatch.setattr(smoke_real_quant_top500, "run_real_quant_top500", fake_run_real_quant_top500)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "smoke_real_quant_top500.py",
            "--trade-date",
            "2026-07-08",
            "--max-lookback-days",
            "20",
            "--akshare-no-proxy",
        ],
    )

    assert smoke_real_quant_top500.main() == 0
    assert captured["trade_date"] == "2026-07-08"
    assert captured["max_lookback_days"] == 20
    assert captured["akshare_no_proxy"] is True


def test_smoke_report_includes_proxy_and_baostock_date_attempts(monkeypatch, tmp_path) -> None:
    fake = types.SimpleNamespace(
        login=lambda: FakeLoginResult(),
        logout=lambda: None,
        query_all_stock=lambda day: FakeQueryResult([["sz.000001", "Ping An Bank"]]),
        query_history_k_data_plus=lambda *args, **kwargs: FakeQueryResult(
            [["2026-07-08", "sz.000001", "10", "11", "9", "10.5", "10", "1000", "10500", "1.2", "5"]]
        ),
    )
    monkeypatch.setitem(sys.modules, "baostock", fake)

    report = run_smoke_real_data_sources(
        check_akshare=False,
        check_baostock=True,
        limit=1,
        output=tmp_path / "smoke.json",
        trade_date="2026-07-08",
        max_lookback_days=1,
    )

    assert "akshare_proxy_env_detected" in report
    assert report["baostock_date_attempts"][0]["date"] == "2026-07-08"
    assert report["baostock_actual_trade_date"] == "2026-07-08"
