from __future__ import annotations

import copy
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest
import yaml

from scripts.audit_quant_factor_scores import normalize_code
from scripts.run_quant_shadow_counterfactual import (
    BASE_INPUT_HASH,
    SHADOW_CONFIG,
    SHADOW_MIGRATION,
    amount_to_cny,
    build_universe_audit,
    build_versions,
    compare_versions,
    differentiated_emotion,
    fallback_audit,
    global_emotion,
    initialize_shadow_db,
    percentile_map,
    rank_version,
    score_fixed,
    version_summary,
    workbook_hash_audit,
)


def _base_rows() -> list[dict]:
    return [
        {
            "stock_code": "000001",
            "stock_name": "甲",
            "total_score": 50,
            "technical_score": 60,
            "capital_score": 20,
            "emotion_score": 50,
            "momentum_score": 70,
            "risk_score": 40,
        },
        {
            "stock_code": "000002",
            "stock_name": "乙",
            "total_score": 49,
            "technical_score": 55,
            "capital_score": 30,
            "emotion_score": 50,
            "momentum_score": 60,
            "risk_score": 45,
        },
        {
            "stock_code": "000003",
            "stock_name": "丙",
            "total_score": 48,
            "technical_score": 50,
            "capital_score": 40,
            "emotion_score": 50,
            "momentum_score": 50,
            "risk_score": 50,
        },
    ]


def _histories() -> dict[str, list[dict]]:
    return {
        "000001": [{"trade_date": "20260722", "amount": 100000}],
        "000002": [{"trade_date": "20260722", "amount": 50000}],
        "000003": [{"trade_date": "20260722", "amount": 10000}],
    }


def _shadow_config() -> dict:
    return yaml.safe_load(SHADOW_CONFIG.read_text(encoding="utf-8"))[
        "shadow_quant_factor"
    ]


def test_daily_amount_thousand_cny_conversion() -> None:
    assert amount_to_cny(123.45, "THOUSAND_CNY") == (
        123450.0,
        "MULTIPLIED_BY_1000",
    )


def test_unit_conversion_does_not_double_convert_cny() -> None:
    assert amount_to_cny(123450, "CNY") == (123450.0, "ALREADY_CNY")


def test_unknown_amount_unit_fails_closed() -> None:
    with pytest.raises(ValueError, match="UNKNOWN_AMOUNT_UNIT"):
        amount_to_cny(1, "UNKNOWN")


def test_configured_universe_gate_changes_after_unit_fix() -> None:
    stocks = [
        {
            "ts_code": "000001.SZ",
            "name": "甲",
            "list_status": "L",
        }
    ]
    histories = {
        "000001": [
            {"trade_date": f"202607{index:02d}", "amount": 60000}
            for index in range(1, 21)
        ]
    }
    _, summary = build_universe_audit(
        stocks, histories, {"000001"}, 20, 50_000_000
    )
    assert summary["configured_gate_legacy_unit_pass_count"] == 0
    assert summary["configured_gate_corrected_unit_pass_count"] == 1
    assert summary["configured_gate_flip_count"] == 1


def test_formal_universe_does_not_flip_when_gate_is_unwired() -> None:
    stocks = [{"ts_code": "000001.SZ", "name": "甲", "list_status": "L"}]
    histories = {
        "000001": [
            {"trade_date": f"202607{index:02d}", "amount": 60000}
            for index in range(1, 21)
        ]
    }
    _, summary = build_universe_audit(
        stocks, histories, {"000001"}, 20, 50_000_000
    )
    assert summary["natural_universe_legacy"] == 1
    assert summary["natural_universe_corrected"] == 1
    assert summary["formal_universe_flip_count_after_amount_fix"] == 0


def test_fixed_universe_version_builder_preserves_member_count() -> None:
    rows = _base_rows()
    versions, _ = build_versions(
        rows,
        _histories(),
        {
            "000001": {"volume_ratio": 2, "turnover_rate": 2},
            "000002": {"volume_ratio": 1.5, "turnover_rate": 2},
            "000003": {"volume_ratio": 1, "turnover_rate": 2},
        },
        {"000001": {"net_mf_amount": 100}},
        {"000001": 60, "000002": 50, "000003": 40},
        55,
    )
    assert all(len(value) == 3 for value in versions.values())


def test_real_volume_ratio_changes_s2_capital() -> None:
    versions, details = build_versions(
        _base_rows(),
        _histories(),
        {"000001": {"volume_ratio": 2.0}},
        {"000001": {"net_mf_amount": 100}},
        {"000001": 60, "000002": 50, "000003": 40},
        55,
    )
    s1 = next(row for row in versions["S1_AMOUNT_UNIT_FIX"] if row["stock_code"] == "000001")
    s2 = next(row for row in versions["S2_REAL_VOLUME_RATIO"] if row["stock_code"] == "000001")
    assert s1["capital_score"] != s2["capital_score"]
    assert details["S2_REAL_VOLUME_RATIO"]["000001"]["raw_volume_ratio"] == 2.0


def test_missing_volume_ratio_is_not_fabricated() -> None:
    _, details = build_versions(
        _base_rows(),
        _histories(),
        {},
        {"000001": {"net_mf_amount": 100}},
        {"000001": 60, "000002": 50, "000003": 40},
        55,
    )
    assert (
        details["S2_REAL_VOLUME_RATIO"]["000001"]["volume_ratio_fallback"]
        == "RAW_VOLUME_RATIO_MISSING"
    )


def test_global_emotion_has_zero_cross_sectional_variance() -> None:
    score, _, summary = global_emotion(
        [
            {"ts_code": "000001.SZ", "close": 10, "high": 10, "pct_chg": 1, "amount": 10},
            {"ts_code": "000002.SZ", "close": 9, "high": 9, "pct_chg": -1, "amount": 10},
        ],
        {
            "000001": {"amount": 10},
            "000002": {"amount": 10},
        },
        {},
        _shadow_config()["global_emotion"],
    )
    assert 0 <= score <= 100
    assert summary["cross_sectional_variance"] == 0


def test_global_emotion_constant_shift_does_not_change_rank() -> None:
    rows = _base_rows()
    versions, _ = build_versions(
        rows,
        _histories(),
        {},
        {},
        {"000001": 60, "000002": 50, "000003": 40},
        80,
    )
    assert [
        row["stock_code"] for row in versions["S2_REAL_VOLUME_RATIO"]
    ] == [row["stock_code"] for row in versions["S3_GLOBAL_EMOTION_REAL"]]


def test_differentiated_emotion_produces_stock_variance() -> None:
    config = _shadow_config()["differentiated_emotion"]
    scores, _, _, summary = differentiated_emotion(
        ["000001", "000002", "000003"],
        {
            "000001": {"industry": "银行"},
            "000002": {"industry": "银行"},
            "000003": {"industry": "化工"},
        },
        {
            "000001": {"pct_chg": 5, "amount": 200, "close": 10, "high": 10},
            "000002": {"pct_chg": 1, "amount": 100, "close": 10, "high": 10},
            "000003": {"pct_chg": -2, "amount": 50, "close": 10, "high": 10},
        },
        {
            "000001": {"amount": 100},
            "000002": {"amount": 100},
            "000003": {"amount": 100},
        },
        {},
        config,
    )
    assert len(set(scores.values())) > 1
    assert summary["variance"] > 0


def test_industry_join_coverage_is_explicit() -> None:
    _, _, _, summary = differentiated_emotion(
        ["000001", "000002"],
        {"000001": {"industry": "银行"}, "000002": {"industry": ""}},
        {
            "000001": {"pct_chg": 1, "amount": 1, "close": 1, "high": 1},
            "000002": {"pct_chg": 2, "amount": 1, "close": 1, "high": 1},
        },
        {"000001": {"amount": 1}, "000002": {"amount": 1}},
        {},
        _shadow_config()["differentiated_emotion"],
    )
    assert summary["industry_join_coverage"] == 0.5


def test_stock_code_format_join_is_normalized() -> None:
    assert normalize_code("000001.SZ") == normalize_code("000001")


def test_moneyflow_fallback_denominator_is_explicit() -> None:
    rows, summary = fallback_audit(
        ["000001", "000002"],
        {
            "000001": [{"amount": 1}],
            "000002": [{"amount": 1}],
        },
        {"000001": {"turnover_rate": 1}},
        {"000001": {"net_mf_amount": 1}},
    )
    assert summary["capital_fallback_stock_count"] == 2
    assert summary["capital_full_fallback_count"] == 0
    assert summary["capital_fallback_cell_count"] == 4
    assert len(rows) > summary["capital_fallback_cell_count"]


def test_percentile_map_preserves_ties() -> None:
    scores = percentile_map({"a": 1, "b": 1, "c": 2})
    assert scores["a"] == scores["b"]
    assert scores["c"] == 100


def test_s0_input_rows_are_not_mutated() -> None:
    rows = _base_rows()
    snapshot = copy.deepcopy(rows)
    build_versions(
        rows,
        _histories(),
        {},
        {},
        {"000001": 60, "000002": 50, "000003": 40},
        55,
    )
    assert rows == snapshot


def test_shadow_migration_contains_only_shadow_tables() -> None:
    text = SHADOW_MIGRATION.read_text(encoding="utf-8").lower()
    assert "create table if not exists quant_shadow_run" in text
    assert "insert into quant_run" not in text
    assert "update quant_rank_result" not in text


def test_shadow_database_is_separate_and_has_required_tables(tmp_path: Path) -> None:
    database = tmp_path / "shadow.db"
    connection = initialize_shadow_db(database)
    try:
        names = {
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type='table'"
            )
        }
    finally:
        connection.close()
    assert {
        "quant_shadow_run",
        "quant_shadow_score",
        "quant_shadow_factor_detail",
        "quant_shadow_universe_audit",
        "quant_shadow_comparison",
        "quant_data_quality_audit",
    } <= names


def test_shadow_run_trigger_blocks_update(tmp_path: Path) -> None:
    connection = initialize_shadow_db(tmp_path / "shadow.db")
    try:
        connection.execute(
            "insert into quant_shadow_run values (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "r",
                "b",
                "2026-07-22",
                "v",
                BASE_INPUT_HASH,
                "c",
                "u",
                "n",
                "FIXED",
                1,
                "p",
                "now",
            ),
        )
        connection.commit()
        with pytest.raises(sqlite3.IntegrityError, match="IMMUTABLE_SHADOW_RUN"):
            connection.execute(
                "update quant_shadow_run set universe_count=2 where shadow_run_id='r'"
            )
    finally:
        connection.close()


def test_all_versions_use_same_declared_input_hash() -> None:
    config = _shadow_config()
    assert config["input_hash"] == BASE_INPUT_HASH
    assert len(set([config["input_hash"] for _ in config["versions"]])) == 1


def test_rank_version_has_deterministic_stock_code_tie_break() -> None:
    rows = [
        {"stock_code": "000002", "total_score": 50},
        {"stock_code": "000001", "total_score": 50},
    ]
    ranked = rank_version("x", rows)
    assert [row["stock_code"] for row in ranked] == ["000001", "000002"]


def test_top_overlap_and_rank_correlations() -> None:
    base = rank_version(
        "S0",
        [
            {
                "stock_code": f"{index:06d}",
                "total_score": 200 - index,
                "technical_score": 50,
                "capital_score": 50,
                "emotion_score": 50,
                "momentum_score": 50,
                "risk_score": 50,
            }
            for index in range(1, 121)
        ],
    )
    candidate = rank_version("S1", list(reversed(base)))
    stock = {row["stock_code"]: {"industry": "A"} for row in base}
    base_summary = version_summary("S0", base, stock)
    candidate_summary = version_summary("S1", candidate, stock)
    comparison = compare_versions(
        base, candidate, base_summary, candidate_summary
    )
    assert comparison["spearman"] == pytest.approx(1)
    assert comparison["kendall"] == pytest.approx(1)
    assert comparison["top100_overlap"] == 1


def test_workbook_content_and_style_hashes_are_separate(tmp_path: Path) -> None:
    workbook = tmp_path / "book.xlsx"
    with zipfile.ZipFile(workbook, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            '<workbook><sheets><sheet name="Sheet1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet><sheetData><row><c s="1"><v>1</v></c></row></sheetData></worksheet>',
        )
        archive.writestr("xl/styles.xml", "<styleSheet/>")
    result = workbook_hash_audit(workbook, "different")
    assert result["cell_content_hash"] != result["style_hash"]
    assert result["diagnosis"] == "ORIGINAL_OVERWRITTEN"


def test_shadow_config_preserves_formal_weights() -> None:
    weights = _shadow_config()["weights"]
    assert weights == {
        "technical": 0.25,
        "capital": 0.25,
        "emotion": 0.20,
        "momentum": 0.15,
        "risk": 0.15,
    }
    assert sum(weights.values()) == 1


def test_no_external_api_llm_order_or_scheduler_in_shadow_config() -> None:
    safety = _shadow_config()["safety"]
    assert safety["external_api_calls"] == 0
    assert safety["llm_calls"] == 0
    assert safety["orders"] == 0
    assert safety["scheduler"] is False
