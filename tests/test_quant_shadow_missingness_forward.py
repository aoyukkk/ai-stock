from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook

from quant.shadow.forward_evaluation import (
    evaluate_forward_rows,
    promotion_status,
    validate_same_execution_basis,
)
from quant.shadow.missingness_policy import (
    DataPipelineError,
    FACTOR_VERSION,
    build_s2_1_missingness_policy,
)
from quant.shadow.threshold_audit import audit_threshold_dependencies
from reporting.immutable_workbook import (
    assert_new_workbook_path,
    versioned_workbook_path,
    workbook_content_style_hashes,
)


def _s2(code: str, rank: int, capital: float = 50, total: float = 50):
    return {
        "stock_code": code,
        "rank": rank,
        "technical_score": 50,
        "capital_score": capital,
        "emotion_score": 50,
        "momentum_score": 50,
        "risk_score": 50,
        "total_score": total,
    }


def _daily(code: str, *, amount: float = 100000, vol: float = 10000):
    return {
        "ts_code": f"{code}.SZ",
        "trade_date": "20260722",
        "amount": amount,
        "vol": vol,
    }


def _basic(code: str, *, volume_ratio: float = 1.2, turnover_rate: float = 3):
    return {
        "ts_code": f"{code}.SZ",
        "trade_date": "20260722",
        "volume_ratio": volume_ratio,
        "turnover_rate": turnover_rate,
    }


def _flow(code: str, value: float = 1000):
    return {
        "ts_code": f"{code}.SZ",
        "trade_date": "20260722",
        "net_mf_amount": value,
    }


def test_normal_moneyflow_missing_reweights_valid_items_without_neutral_50():
    code = "000001"
    result = build_s2_1_missingness_policy(
        [_s2(code, 1, capital=50, total=50)],
        daily_by_code={code: _daily(code)},
        basic_by_code={code: _basic(code)},
        flow_by_code={},
        histories={code: [_daily(code)] * 20},
    )
    audit = result.audit_rows[0]
    assert audit["missing_class"] == "NORMAL_SOURCE_MISSING"
    assert audit["capital_data_coverage"] == 0.75
    assert audit["capital_confidence"] == "REWEIGHTED_NORMAL_MISSING"
    assert audit["reweighted"] is True
    missing = next(
        row for row in result.factor_rows if row["factor_name"] == "main_inflow_score"
    )
    assert missing["weight"] == 0
    assert missing["score"] == 0
    assert missing["score_origin"] == "NO_NEUTRAL_SCORE_APPLIED"
    assert result.ranked[0]["factor_version"] == FACTOR_VERSION


def test_complete_moneyflow_preserves_s2_capital_and_uses_real_main_inflow():
    code = "000001"
    result = build_s2_1_missingness_policy(
        [_s2(code, 1, capital=62.5, total=55)],
        daily_by_code={code: _daily(code)},
        basic_by_code={code: _basic(code)},
        flow_by_code={code: _flow(code)},
        histories={code: [_daily(code)] * 20},
    )
    assert result.audit_rows[0]["missing_class"] == "COMPLETE"
    assert result.audit_rows[0]["capital_data_coverage"] == 1
    assert result.ranked[0]["capital_score"] == 62.5
    inflow = next(
        row for row in result.factor_rows if row["factor_name"] == "main_inflow_score"
    )
    assert inflow["raw_value"] == 1000
    assert inflow["weight"] == 0.25


def test_non_random_and_data_insufficient_are_separate_and_not_reweighted():
    rows = [_s2("000001", 1), _s2("000002", 2)]
    result = build_s2_1_missingness_policy(
        rows,
        daily_by_code={},
        basic_by_code={},
        flow_by_code={},
        histories={
            "000001": [_daily("000001")] * 25,
            "000002": [_daily("000002")] * 15,
        },
    )
    by_code = {row["stock_code"]: row for row in result.audit_rows}
    assert by_code["000001"]["missing_class"] == "NON_RANDOM_MISSING"
    assert by_code["000002"]["missing_class"] == "DATA_INSUFFICIENT"
    assert not by_code["000001"]["reweighted"]
    assert not by_code["000002"]["comparison_eligible"]
    assert result.summary["non_random_missing_count"] == 2
    assert result.summary["data_insufficient_count"] == 1


def test_pipeline_error_fails_closed_and_never_becomes_neutral():
    code = "000001"
    with pytest.raises(DataPipelineError, match="DUPLICATE"):
        build_s2_1_missingness_policy(
            [_s2(code, 1)],
            daily_by_code={code: _daily(code)},
            basic_by_code={code: _basic(code)},
            flow_by_code={},
            histories={code: [_daily(code)] * 20},
            pipeline_errors={code: "DAILY_DUPLICATE_ROWS:2"},
        )


def test_every_stock_has_four_capital_detail_rows_and_amount_lineage():
    codes = ["000001", "000002", "000003"]
    result = build_s2_1_missingness_policy(
        [_s2(code, index) for index, code in enumerate(codes, 1)],
        daily_by_code={code: _daily(code) for code in codes},
        basic_by_code={code: _basic(code) for code in codes},
        flow_by_code={code: _flow(code) for code in codes},
        histories={code: [_daily(code)] * 20 for code in codes},
    )
    assert len(result.factor_rows) == 12
    assert {row["stock_code"] for row in result.factor_rows} == set(codes)
    assert len(result.amount_lineage_rows) == 3
    assert all(row["amount_raw_unit"] == "THOUSAND_CNY" for row in result.amount_lineage_rows)
    assert all(row["amount_cny"] == 100000000 for row in result.amount_lineage_rows)


def test_threshold_audit_marks_rank_and_absolute_dependencies_and_counts_flips():
    versions = {
        "S0_LEGACY": [_s2("000001", 1, total=44), _s2("000002", 2, total=46)],
        "S1_AMOUNT_UNIT_FIX": [_s2("000001", 1, total=45), _s2("000002", 2, total=46)],
        "S2_REAL_VOLUME_RATIO": [_s2("000001", 1, total=44), _s2("000002", 2, total=46)],
        "S2_1_MISSINGNESS_POLICY": [_s2("000001", 1, total=44), _s2("000002", 2, total=46)],
        "S3_GLOBAL_EMOTION_REAL": [_s2("000001", 1, total=43), _s2("000002", 2, total=44)],
        "S3_DIFFERENTIATED_EMOTION": [_s2("000001", 1, total=60), _s2("000002", 2, total=46)],
        "S4_PERCENTILE_NORMALIZATION": [_s2("000001", 1, total=80), _s2("000002", 2, total=20)],
    }
    rows = audit_threshold_dependencies(Path(__file__).parents[1], versions)
    entry = next(row for row in rows if row["dependency_key"] == "ENTRY_TIMING_V1_QUANT_GATE")
    flash = next(row for row in rows if row["dependency_key"] == "FLASH_INPUT_POOL")
    assert entry["absolute_score_selected"] is True
    assert entry["s1_flip_count"] == 1
    assert entry["s3_global_flip_count"] == 1
    assert flash["rank_selected"] is True
    assert flash["s1_flip_count"] is None


def test_workbook_path_includes_run_and_version_and_refuses_overwrite(tmp_path):
    path = versioned_workbook_path(
        tmp_path,
        stem="智能交易助手",
        trade_date="2026-07-22",
        run_id="quant-run:abc",
        factor_version="v0.3-phase4",
    )
    assert "quant-run-abc" in path.name
    assert "v0.3-phase4" in path.name
    path.write_bytes(b"x")
    with pytest.raises(FileExistsError, match="IMMUTABLE_WORKBOOK"):
        assert_new_workbook_path(path)


def test_workbook_content_and_style_hashes_are_separate(tmp_path):
    path = tmp_path / "test.xlsx"
    workbook = Workbook()
    workbook.active["A1"] = "value"
    workbook.save(path)
    workbook.close()
    hashes = workbook_content_style_hashes(path)
    assert len(hashes["content_hash"]) == 64
    assert len(hashes["style_hash"]) == 64
    assert hashes["content_hash"] != hashes["style_hash"]


def test_forward_evaluation_requires_same_execution_basis_and_calculates_metrics():
    base = {
        "trade_date": "2026-07-27",
        "stock_code": "000001",
        "entry_trade_date": "2026-07-28",
        "entry_price": 10,
        "entry_status": "FILLED",
        "execution_policy": "NEXT_OPEN",
        "return_d1": 0.1,
        "return_d3": 0.2,
        "return_d5": 0.3,
        "mfe": 0.4,
        "mae": -0.05,
    }
    rows = [
        {**base, "version_key": "S0_LEGACY", "rank": 1},
        {**base, "version_key": "S2_REAL_VOLUME_RATIO", "rank": 1},
    ]
    result = evaluate_forward_rows(rows, top_sizes=(20,))
    assert len(result) == 6
    assert result[0]["execution_policy"] == "NEXT_OPEN"
    assert "top_bottom_spread" in result[0]
    assert "incremental_replacement_return" in result[0]
    bad = [rows[0], {**rows[1], "entry_price": 10.1}]
    with pytest.raises(ValueError, match="EXECUTION_BASIS_CONFLICT"):
        validate_same_execution_basis(bad)


def test_promotion_is_keep_legacy_until_all_gates_pass():
    assert (
        promotion_status(
            trading_days=19,
            complete_d3_tradable_samples=100,
            timing_contract_failures=0,
            data_coverage_explainable=True,
            stable_vs_s0=True,
        )
        == "KEEP_LEGACY_AND_SHADOW"
    )
    assert (
        promotion_status(
            trading_days=20,
            complete_d3_tradable_samples=100,
            timing_contract_failures=0,
            data_coverage_explainable=True,
            stable_vs_s0=True,
        )
        == "ELIGIBLE_FOR_MANUAL_PROMOTION_REVIEW"
    )


def test_research_migration_has_shadow_tables_only():
    sql = (
        Path(__file__).parents[1]
        / "database"
        / "migrations"
        / "20260724_quant_shadow_forward_v1.sql"
    ).read_text(encoding="utf-8")
    assert "quant_shadow_forward_sample" in sql
    assert "quant_shadow_missingness" in sql
    assert "INSERT INTO quant_run" not in sql
    assert "INSERT INTO quant_rank_result" not in sql
    assert "INSERT INTO stock_factor_score" not in sql


def test_future_official_quant_persists_full_scored_universe():
    script = (
        Path(__file__).parents[1] / "scripts" / "run_real_quant_top500.py"
    ).read_text(encoding="utf-8")
    assert '"results": results' in script
    assert "save_factor_scores(session, persistence_ranking)" in script


def test_frontend_contains_legacy_uncalibrated_score_disclosure():
    root = Path(__file__).parents[1] / "frontend" / "src"
    text = (root / "pages" / "QuantScan.vue").read_text(encoding="utf-8")
    workbench = (root / "views" / "ResultTableView.vue").read_text(encoding="utf-8")
    assert "Legacy 未校准分数" in text
    assert "不等同于收益概率" in workbench
