from __future__ import annotations

from quant.shadow.tushare_quant_v2 import (
    FACTOR_VERSION,
    apply_concentration,
    build_validation,
    capital_scores,
    emotion_scores,
    global_regime,
    risk_scores,
    technical_score,
)
from quant.shadow.data_gate_recovery import (
    factor_cache_audit,
    fetch_ths_members_by_board,
)


def _history(code: str, *, count: int = 30, start: float = 10.0):
    return [
        {
            "ts_code": f"{code}.SZ",
            "trade_date": f"202606{i + 1:02d}",
            "open": start + i * 0.05,
            "high": start + i * 0.05 + 0.2,
            "low": start + i * 0.05 - 0.1,
            "close": start + i * 0.05 + 0.1,
            "amount": 100000 + i * 1000,
            "vol": 10000 + i * 100,
            "pct_chg": 0.5,
        }
        for i in range(count)
    ]


def test_technical_corrected_substructure_has_five_components() -> None:
    score, detail = technical_score(_history("000001"))
    assert 0 <= score <= 100
    assert set(detail) == {
        "trend_structure",
        "price_position",
        "pattern_quality",
        "volume_price_confirmation",
        "volatility_adaptation",
    }


def test_capital_uses_actual_volume_ratio_and_reweights_normal_missing() -> None:
    daily = {
        "000001": {"amount": 100000, "trade_date": "20260724"},
        "000002": {"amount": 100000, "trade_date": "20260724"},
    }
    basic = {
        "000001": {"volume_ratio": 0.6, "turnover_rate": 2, "trade_date": "20260724"},
        "000002": {"volume_ratio": 2.5, "turnover_rate": 2, "trade_date": "20260724"},
    }
    scores, details = capital_scores(
        ["000001", "000002"], daily, basic, {}, {}
    )
    assert scores["000002"] > scores["000001"]
    assert details["000001"]["capital_confidence"] == "REWEIGHTED_NORMAL_MISSING"
    assert "main_inflow" in details["000001"]["missing_subfactors"]
    assert details["000001"]["amount_cny"] == 100000000


def test_global_emotion_is_regime_only_and_stock_emotion_differs() -> None:
    daily_rows = [
        {"ts_code": "000001.SZ", "pct_chg": 2},
        {"ts_code": "000002.SZ", "pct_chg": -1},
    ]
    regime = global_regime(daily_rows, [])
    assert regime["rank_contribution"] == 0
    stock = {"000001": {"industry": "A"}, "000002": {"industry": "A"}}
    daily = {
        "000001": {"pct_chg": 2},
        "000002": {"pct_chg": -1},
    }
    scores, detail = emotion_scores(
        ["000001", "000002"], stock, daily, [], []
    )
    assert scores["000001"] != scores["000002"]
    assert all(row["global_emotion_rank_contribution"] == 0 for row in detail.values())


def test_risk_hard_gate_and_soft_health_are_separate() -> None:
    codes = ["000001", "000002"]
    stock = {
        "000001": {"name": "*ST样本"},
        "000002": {"name": "普通样本"},
    }
    daily = {
        code: {"amount": 100000, "vol": 10000, "close": 11} for code in codes
    }
    basic = {code: {"turnover_rate": 2} for code in codes}
    histories = {code: _history(code) for code in codes}
    scores, detail = risk_scores(codes, stock, daily, basic, histories, {})
    assert detail["000001"]["hard_gate"] is True
    assert scores["000001"] == detail["000001"]["soft_risk_health"]
    assert detail["000002"]["hard_gate"] is False


def test_concentration_is_shadow_only_and_enforces_industry_cap() -> None:
    ranked = [
        {"stock_code": f"{index:06d}", "total_score": 100 - index, "hard_gate": False}
        for index in range(6)
    ]
    stock = {
        f"{index:06d}": {"industry": "A" if index < 4 else "B"}
        for index in range(6)
    }
    selected, decisions = apply_concentration(
        ranked, stock, regime="NEUTRAL", target=4
    )
    assert sum(stock[row["stock_code"]]["industry"] == "A" for row in selected) <= 2
    assert any("INDUSTRY_MAX_2" in row["reasons"] for row in decisions)
    assert FACTOR_VERSION == "TUSHARE_QUANT_V2_CORRECTED_SHADOW"


def test_stk_factor_partial_cache_is_detected_and_not_a_hard_gate() -> None:
    daily = [{"ts_code": "000001.SZ"}, {"ts_code": "000002.SZ"}]
    raw = [
        {"ts_code": "000001.SZ", "trade_date": "20260724"},
        {"ts_code": "000002.SZ", "trade_date": "20260724"},
    ]
    cached = [{"ts_code": "000001.SZ", "trade_date": "20260724"}]
    audit = factor_cache_audit(
        raw_rows=raw,
        cached_rows=cached,
        daily_rows=daily,
    )
    assert audit["partial_cache_confirmed"] is True
    assert audit["raw_missing_codes"] == []
    assert audit["cache_missing_codes_before_refresh"] == ["000002.SZ"]


def test_ths_members_are_fetched_per_board_without_pagination(tmp_path) -> None:
    class Provider:
        def query_endpoint(self, _name, *, params, fields, use_cache, write_cache):
            assert set(params) == {"ts_code"}
            assert "is_new" in fields
            assert use_cache is True
            assert write_cache is True
            return type(
                "Result",
                (),
                {
                    "status": "available",
                    "error_type": None,
                    "records": [
                        {
                            "ts_code": params["ts_code"],
                            "con_code": "000001.SZ",
                            "is_new": "Y",
                        },
                        {
                            "ts_code": params["ts_code"],
                            "con_code": "000002.SZ",
                            "is_new": "N",
                        },
                    ],
                },
            )()

    rows, audit = fetch_ths_members_by_board(
        Provider(),
        [{"ts_code": "881001.TI", "name": "A"}],
        category="industry",
        output_root=tmp_path,
    )
    assert len(rows) == 1
    assert rows[0]["con_code"] == "000001.SZ"
    assert audit["pagination_used"] is False
    assert audit["historical_rows_removed"] == 1


def test_build_validation_uses_local_technical_when_pro_is_missing() -> None:
    histories = {
        "000001": _history("000001"),
        "000002": _history("000002", start=9),
    }
    daily = {
        item: {
            **rows[-1],
            "volume_ratio": 1.2,
            "amount": 100000,
            "vol": 10000,
        }
        for item, rows in histories.items()
    }
    basic = {
        item: {"volume_ratio": 1.2, "turnover_rate": 2}
        for item in histories
    }
    stock = {
        "000001": {"name": "A", "industry": "I"},
        "000002": {"name": "B", "industry": "I"},
    }
    legacy = [
        {
            "stock_code": item,
            "stock_name": item,
            "technical_score": 50,
            "capital_score": 50,
            "emotion_score": 50,
            "momentum_score": 50,
            "risk_score": 50,
            "total_score": 50,
        }
        for item in histories
    ]
    result = build_validation(
        legacy,
        histories=histories,
        stock_by_code=stock,
        daily_by_code=daily,
        basic_by_code=basic,
        flow_by_code={},
        ths_flow_by_code={},
        limit_by_code={},
        limit_rows=[],
        ths_sector_flow=[],
        pro_factor_codes={"000001"},
    )
    final = {
        row["stock_code"]: row for row in result.stages[FACTOR_VERSION]
    }
    assert final["000001"]["technical_confidence"] == "HIGH"
    assert final["000002"]["technical_confidence"] == "DEGRADED"
    assert final["000002"]["technical_fallback_status"] == "PRO_FACTOR_MISSING"
