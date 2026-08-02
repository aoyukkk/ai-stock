from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quant.shadow.forward_evaluation import PROMOTION_CONCLUSION, promotion_status
from quant.shadow.missingness_policy import (
    FACTOR_VERSION,
    VERSION_KEY,
    build_s2_1_missingness_policy,
)
from quant.shadow.threshold_audit import audit_threshold_dependencies
from reporting.immutable_workbook import versioned_workbook_path
from scripts.audit_quant_factor_scores import canonical_hash, file_sha256, normalize_code
from scripts.run_quant_shadow_counterfactual import (
    BASE_DATABASE,
    BASE_INPUT_HASH,
    BASE_REPORT,
    BASE_RUN_ID,
    BASE_WORKBOOK,
    SHADOW_MIGRATION,
    STOCK_BASIC_CACHE,
    TRADE_DATE,
    by_code,
    cache_rows,
    compare_versions,
    daily_history,
    source_hashes,
    version_summary,
)


PHASE = "Quant Bugfix Candidate S2.1 + Missingness Policy + Forward Blind Evaluation"
CONFIG_PATH = ROOT / "config" / "quant_shadow_forward_v1.yaml"
MIGRATION_PATH = (
    ROOT
    / "database"
    / "migrations"
    / "20260724_quant_shadow_forward_v1.sql"
)
PRIOR_AUDIT = (
    ROOT
    / "outputs"
    / "quant_factor_shadow_research"
    / TRADE_DATE
    / "quant_remediation_audit.json"
)
RESEARCH_DB = ROOT / "data" / "quant_shadow_research.db"
DEFAULT_OUTPUT_ROOT = ROOT / "outputs" / "quant_bugfix_candidate_s2_1" / TRADE_DATE
FROZEN_VERSIONS = (
    "S0_LEGACY",
    "S2_REAL_VOLUME_RATIO",
    VERSION_KEY,
    "S3_DIFFERENTIATED_EMOTION",
    "S4_PERCENTILE_NORMALIZATION",
)


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _write_csv(path: Path, records: Iterable[Mapping[str, Any]]) -> None:
    rows = [dict(row) for row in records]
    headers: list[str] = []
    for row in rows:
        for key in row:
            if key not in headers:
                headers.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers or ["status"])
        writer.writeheader()
        for row in rows or [{"status": "NO_DATA"}]:
            writer.writerow(
                {
                    key: json.dumps(value, ensure_ascii=False)
                    if isinstance(value, (list, dict))
                    else value
                    for key, value in row.items()
                }
            )


def _row_dicts(
    connection: sqlite3.Connection, sql: str, parameters: tuple[Any, ...] = ()
) -> list[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    return [dict(row) for row in connection.execute(sql, parameters).fetchall()]


def _version_rows(
    connection: sqlite3.Connection, shadow_run_id: str
) -> list[dict[str, Any]]:
    return _row_dicts(
        connection,
        """
        SELECT stock_code,technical_score,capital_score,emotion_score,
               momentum_score,risk_score,total_score,rank
        FROM quant_shadow_score
        WHERE shadow_run_id=?
        ORDER BY rank
        """,
        (shadow_run_id,),
    )


def _old_run_snapshot(
    connection: sqlite3.Connection, run_ids: Iterable[str]
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for run_id in run_ids:
        run = connection.execute(
            "SELECT immutable_payload_hash,input_hash,config_hash FROM quant_shadow_run "
            "WHERE shadow_run_id=?",
            (run_id,),
        ).fetchone()
        result[run_id] = {
            "run": tuple(run) if run else None,
            "score_count": connection.execute(
                "SELECT COUNT(*) FROM quant_shadow_score WHERE shadow_run_id=?",
                (run_id,),
            ).fetchone()[0],
            "detail_count": connection.execute(
                "SELECT COUNT(*) FROM quant_shadow_factor_detail WHERE shadow_run_id=?",
                (run_id,),
            ).fetchone()[0],
        }
    return result


def _cache_pipeline_errors(
    records: list[dict[str, Any]], *, dataset: str
) -> dict[str, str]:
    counts: Counter[str] = Counter()
    errors: dict[str, str] = {}
    for row in records:
        raw = row.get("ts_code") or row.get("stock_code")
        code = normalize_code(raw)
        if not code or len(code) != 6:
            continue
        counts[code] += 1
        if str(row.get("trade_date") or "") != "20260722":
            errors[code] = f"{dataset.upper()}_TRADE_DATE_MISMATCH"
    for code, count in counts.items():
        if count > 1:
            errors[code] = f"{dataset.upper()}_DUPLICATE_ROWS:{count}"
    return errors


def _s2_1_payload_hash(
    ranked: list[dict[str, Any]],
    audits: list[dict[str, Any]],
    config_hash: str,
    engine_hash: str,
) -> str:
    return canonical_hash(
        {
            "version": VERSION_KEY,
            "input_hash": BASE_INPUT_HASH,
            "config_hash": config_hash,
            "engine_hash": engine_hash,
            "scores": [
                (
                    row["stock_code"],
                    row["rank"],
                    row["capital_score"],
                    row["total_score"],
                )
                for row in ranked
            ],
            "missingness": [
                (
                    row["stock_code"],
                    row["missing_class"],
                    row["capital_data_coverage"],
                    row["capital_confidence"],
                )
                for row in audits
            ],
        }
    )


def _persist_s2_1(
    connection: sqlite3.Connection,
    *,
    result,
    s0_rows: list[dict[str, Any]],
    stock_by_code: dict[str, dict[str, Any]],
    s2_run_id: str,
    config_hash: str,
    engine_hash: str,
) -> dict[str, Any]:
    created_at = datetime.now(timezone.utc).isoformat()
    universe_snapshot_id = "sha256:" + canonical_hash(
        sorted(row["stock_code"] for row in result.ranked)
    )
    payload_hash = _s2_1_payload_hash(
        result.ranked, result.audit_rows, config_hash, engine_hash
    )
    shadow_run_id = f"shadow-s2_1_missingness_policy-{payload_hash[:16]}"
    existing = connection.execute(
        "SELECT immutable_payload_hash FROM quant_shadow_run WHERE shadow_run_id=?",
        (shadow_run_id,),
    ).fetchone()
    if existing:
        if existing[0] != payload_hash:
            raise RuntimeError("IMMUTABLE_S2_1_HASH_CONFLICT")
        return {
            "shadow_run_id": shadow_run_id,
            "factor_version": FACTOR_VERSION,
            "universe_snapshot_id": universe_snapshot_id,
            "immutable_payload_hash": payload_hash,
            "persistence": "REUSED_IDENTICAL_IMMUTABLE_RUN",
        }

    s2_summary = version_summary(VERSION_KEY, result.ranked, stock_by_code)
    s0_summary = version_summary("S0_LEGACY", s0_rows, stock_by_code)
    comparison = compare_versions(s0_rows, result.ranked, s0_summary, s2_summary)
    connection.execute(
        "INSERT INTO quant_shadow_run VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            shadow_run_id,
            BASE_RUN_ID,
            TRADE_DATE,
            FACTOR_VERSION,
            BASE_INPUT_HASH,
            config_hash,
            universe_snapshot_id,
            "PER_STOCK_FIXED_RANGE_MISSINGNESS_POLICY",
            "FIXED",
            len(result.ranked),
            payload_hash,
            created_at,
        ),
    )
    connection.executemany(
        "INSERT INTO quant_shadow_score VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                shadow_run_id,
                BASE_RUN_ID,
                TRADE_DATE,
                row["stock_code"],
                FACTOR_VERSION,
                BASE_INPUT_HASH,
                config_hash,
                universe_snapshot_id,
                "PER_STOCK_FIXED_RANGE_MISSINGNESS_POLICY",
                row["technical_score"],
                row["capital_score"],
                row["emotion_score"],
                row["momentum_score"],
                row["risk_score"],
                row["total_score"],
                row["rank"],
                created_at,
            )
            for row in result.ranked
        ],
    )
    factor_rows = []
    for row in result.ranked:
        for factor, weight in (
            ("technical", 0.25),
            ("capital", 0.25),
            ("emotion", 0.20),
            ("momentum", 0.15),
            ("risk", 0.15),
        ):
            score = float(row[f"{factor}_score"])
            factor_rows.append(
                (
                    shadow_run_id,
                    row["stock_code"],
                    factor,
                    f"{factor}_group_score",
                    score,
                    score,
                    score,
                    weight,
                    round(score * weight, 4),
                    None,
                    None,
                    "SHADOW_GROUP_SCORE",
                    FACTOR_VERSION,
                    BASE_INPUT_HASH,
                    universe_snapshot_id,
                    created_at,
                )
            )
    for detail in result.factor_rows:
        factor_rows.append(
            (
                shadow_run_id,
                detail["stock_code"],
                detail["factor_group"],
                detail["factor_name"],
                detail["raw_value"],
                detail["normalized_value"],
                detail["score"],
                detail["weight"],
                detail["contribution"],
                detail["missing_reason"],
                detail["fallback_type"],
                detail["score_origin"],
                FACTOR_VERSION,
                BASE_INPUT_HASH,
                universe_snapshot_id,
                created_at,
            )
        )
    connection.executemany(
        "INSERT INTO quant_shadow_factor_detail VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        factor_rows,
    )
    connection.executemany(
        "INSERT INTO quant_shadow_missingness VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                shadow_run_id,
                row["stock_code"],
                row["missing_class"],
                row["missing_reason"],
                json.dumps(row["missing_subfactors"], ensure_ascii=False),
                json.dumps(row["valid_subfactors"], ensure_ascii=False),
                row["capital_data_coverage"],
                row["capital_confidence"],
                int(row["reweighted"]),
                int(row["comparison_eligible"]),
                row["score_status"],
                created_at,
            )
            for row in result.audit_rows
        ],
    )
    connection.executemany(
        "INSERT INTO quant_shadow_data_lineage VALUES (?,?,?,?,?,?,?,?)",
        [
            (
                shadow_run_id,
                row["stock_code"],
                row["amount_raw"],
                row["amount_raw_unit"],
                row["amount_cny"],
                row["unit_conversion_version"],
                BASE_INPUT_HASH,
                created_at,
            )
            for row in result.amount_lineage_rows
        ],
    )
    connection.executemany(
        "INSERT INTO quant_data_quality_audit VALUES (?,?,?,?,?,?,?,?)",
        [
            (
                shadow_run_id,
                row["stock_code"],
                "capital",
                "moneyflow",
                row["missing_class"],
                row["missing_reason"],
                row["capital_confidence"],
                created_at,
            )
            for row in result.audit_rows
            if row["missing_class"] != "COMPLETE"
        ],
    )
    connection.execute(
        """
        INSERT INTO quant_shadow_universe_audit
        SELECT ?,stock_code,stock_name,exclusion_stage,exclusion_reason,
               raw_amount,normalized_amount,threshold,unit_fix_changes_result,
               final_status,?
        FROM quant_shadow_universe_audit WHERE shadow_run_id=?
        """,
        (shadow_run_id, created_at, s2_run_id),
    )
    connection.execute(
        "INSERT INTO quant_shadow_comparison VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            shadow_run_id,
            BASE_RUN_ID,
            comparison["spearman"],
            comparison["kendall"],
            comparison["mean_rank_delta"],
            comparison["median_rank_delta"],
            comparison["max_rank_delta"],
            comparison["top20_overlap"],
            comparison["top50_overlap"],
            comparison["top100_overlap"],
            comparison["top100_flip_count"],
            comparison["sector_concentration_delta"],
            created_at,
        ),
    )
    connection.commit()
    return {
        "shadow_run_id": shadow_run_id,
        "factor_version": FACTOR_VERSION,
        "universe_snapshot_id": universe_snapshot_id,
        "immutable_payload_hash": payload_hash,
        "persistence": "INSERTED_IMMUTABLE_RUN",
    }


def _persist_threshold_audit(
    connection: sqlite3.Connection,
    rows: list[dict[str, Any]],
    config_hash: str,
) -> str:
    audit_id = "threshold-audit-" + canonical_hash(
        {"config_hash": config_hash, "rows": rows}
    )[:16]
    created_at = datetime.now(timezone.utc).isoformat()
    if connection.execute(
        "SELECT 1 FROM quant_shadow_threshold_dependency WHERE audit_id=? LIMIT 1",
        (audit_id,),
    ).fetchone():
        return audit_id
    connection.executemany(
        "INSERT INTO quant_shadow_threshold_dependency VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                audit_id,
                row["dependency_key"],
                row["module"],
                row["source_file"],
                row["source_line"],
                row["threshold_name"],
                row["threshold_value"],
                row["score_field"],
                row["selection_mode"],
                int(row["rank_selected"]),
                int(row["absolute_score_selected"]),
                int(row["global_emotion_shift_can_flip"]),
                row["s1_flip_count"],
                row["s2_flip_count"],
                row["s2_1_flip_count"],
                row["s3_global_flip_count"],
                row["s3_differentiated_flip_count"],
                row["s4_flip_count"],
                row["notes"],
                created_at,
            )
            for row in rows
        ],
    )
    connection.commit()
    return audit_id


def _persist_registry_and_retrospective(
    connection: sqlite3.Connection,
    *,
    config: dict[str, Any],
    config_hash: str,
    versions: dict[str, list[dict[str, Any]]],
    run_meta: dict[str, dict[str, Any]],
    coverage_by_code: dict[str, dict[str, Any]],
) -> tuple[str, int]:
    registry_id = "forward-registry-" + canonical_hash(
        {
            "config_hash": config_hash,
            "effective": config["forward_effective_trade_date"],
            "versions": config["versions"],
        }
    )[:16]
    created_at = datetime.now(timezone.utc).isoformat()
    if not connection.execute(
        "SELECT 1 FROM quant_shadow_version_registry WHERE registry_id=? LIMIT 1",
        (registry_id,),
    ).fetchone():
        connection.executemany(
            "INSERT INTO quant_shadow_version_registry VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    registry_id,
                    key,
                    values["factor_version"],
                    values["tier"],
                    int(values["frozen_for_forward"]),
                    config["retrospective_trade_date"],
                    config["forward_effective_trade_date"],
                    PROMOTION_CONCLUSION,
                    config_hash,
                    created_at,
                )
                for key, values in config["versions"].items()
            ],
        )
    existing = connection.execute(
        "SELECT COUNT(*) FROM quant_shadow_forward_sample WHERE registry_id=? "
        "AND sample_type='RETROSPECTIVE_DIAGNOSTIC'",
        (registry_id,),
    ).fetchone()[0]
    if not existing:
        records = []
        for version in FROZEN_VERSIONS:
            meta = run_meta[version]
            for row in versions[version]:
                code = normalize_code(row["stock_code"])
                coverage = coverage_by_code[code]
                sample_id = "retrospective-" + canonical_hash(
                    [registry_id, version, TRADE_DATE, code]
                )[:24]
                records.append(
                    (
                        sample_id,
                        registry_id,
                        version,
                        meta["factor_version"],
                        "RETROSPECTIVE_DIAGNOSTIC",
                        TRADE_DATE,
                        code,
                        row["rank"],
                        int(row["rank"] <= 20),
                        int(row["rank"] <= 50),
                        int(row["rank"] <= 100),
                        row["technical_score"],
                        row["capital_score"],
                        row["emotion_score"],
                        row["momentum_score"],
                        row["risk_score"],
                        row["total_score"],
                        coverage["capital_data_coverage"],
                        coverage["capital_confidence"],
                        BASE_INPUT_HASH,
                        meta["universe_snapshot_id"],
                        None,
                        None,
                        None,
                        None,
                        "RETROSPECTIVE_NOT_BLIND",
                        created_at,
                    )
                )
        connection.executemany(
            "INSERT INTO quant_shadow_forward_sample VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            records,
        )
        existing = len(records)
    connection.commit()
    return registry_id, existing


def _persist_workbook_artifact(
    connection: sqlite3.Connection,
    *,
    prior: dict[str, Any],
) -> str:
    workbook = prior["workbook_hash_audit"]
    artifact_id = "workbook-artifact-" + canonical_hash(
        {
            "run_id": BASE_RUN_ID,
            "input_hash": BASE_INPUT_HASH,
            "path": workbook["path"],
            "content_hash": workbook["cell_content_hash"],
            "style_hash": workbook["style_hash"],
        }
    )[:16]
    existing = connection.execute(
        "SELECT content_hash,style_hash FROM quant_shadow_workbook_artifact "
        "WHERE artifact_id=?",
        (artifact_id,),
    ).fetchone()
    if existing:
        if tuple(existing) != (
            workbook["cell_content_hash"],
            workbook["style_hash"],
        ):
            raise RuntimeError("IMMUTABLE_WORKBOOK_ARTIFACT_HASH_CONFLICT")
        return artifact_id
    connection.execute(
        "INSERT INTO quant_shadow_workbook_artifact VALUES (?,?,?,?,?,?,?,?)",
        (
            artifact_id,
            BASE_RUN_ID,
            "v0.3-phase4",
            workbook["path"],
            BASE_INPUT_HASH,
            workbook["cell_content_hash"],
            workbook["style_hash"],
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    connection.commit()
    return artifact_id


def _membership_changes(
    before: list[dict[str, Any]], after: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    before_rank = {normalize_code(row["stock_code"]): row for row in before}
    after_rank = {normalize_code(row["stock_code"]): row for row in after}
    for size in (20, 50, 100):
        left = set(list(before_rank)[:size])
        right = set(list(after_rank)[:size])
        for direction, values in (
            ("ENTERED", right - left),
            ("EXITED", left - right),
        ):
            for code in sorted(values):
                output.append(
                    {
                        "top_n": size,
                        "direction": direction,
                        "stock_code": code,
                        "s2_rank": before_rank[code]["rank"],
                        "s2_1_rank": after_rank[code]["rank"],
                    }
                )
    return output


def _markdown(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"# {PHASE}",
            "",
            f"- S2.1: `{report['s2_1_factor_version']}`",
            f"- Moneyflow missing: `{report['moneyflow_missing_count']}`",
            f"- Normal missing: `{report['normal_missing']}`",
            f"- Non-random/data-insufficient: `{report['non_random_missing']}`",
            f"- Pipeline errors: `{report['pipeline_errors']}`",
            f"- S2.1 Top100 overlap: `{report['s2_1_top100_overlap']:.2%}`",
            f"- Retrospective rows: `{report['retrospective_samples']}`",
            f"- True forward rows: `{report['true_forward_samples']}`",
            f"- Promotion conclusion: `{report['promotion_conclusion']}`",
            f"- Final status: `{report['final_status']}`",
            "",
            "No external API, LLM, order, scheduler, production Quant config, or historical Shadow mutation occurred.",
        ]
    )


def run(args: argparse.Namespace) -> dict[str, Any]:
    if not (args.no_external_api and args.no_llm and args.no_orders and args.shadow_only):
        raise RuntimeError("SHADOW_SAFETY_FLAGS_REQUIRED")
    required = (
        CONFIG_PATH,
        MIGRATION_PATH,
        PRIOR_AUDIT,
        RESEARCH_DB,
        BASE_REPORT,
        BASE_DATABASE,
        BASE_WORKBOOK,
    )
    if not all(path.exists() for path in required):
        raise RuntimeError("REQUIRED_LOCAL_INPUT_MISSING")
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))[
        "quant_shadow_forward"
    ]
    prior = _json(PRIOR_AUDIT)
    before = {
        "production_database_size": BASE_DATABASE.stat().st_size,
        "production_database_mtime": BASE_DATABASE.stat().st_mtime_ns,
        "historical_workbook_hash": file_sha256(BASE_WORKBOOK),
        "formal_hashes": source_hashes(),
    }
    old_meta = prior["shadow_runs"]
    old_ids = [value["shadow_run_id"] for value in old_meta.values()]
    connection = sqlite3.connect(RESEARCH_DB)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(SHADOW_MIGRATION.read_text(encoding="utf-8"))
    connection.executescript(MIGRATION_PATH.read_text(encoding="utf-8"))
    connection.commit()
    old_snapshot_before = _old_run_snapshot(connection, old_ids)
    versions = {
        version: _version_rows(connection, meta["shadow_run_id"])
        for version, meta in old_meta.items()
    }
    if any(len(rows) != 5310 for rows in versions.values()):
        raise RuntimeError("HISTORICAL_SHADOW_VERSION_INCOMPLETE")

    daily_records = cache_rows("daily")
    basic_records = cache_rows("daily_basic")
    flow_records = cache_rows("moneyflow")
    pipeline_errors = {}
    for dataset, records in (
        ("daily", daily_records),
        ("daily_basic", basic_records),
        ("moneyflow", flow_records),
    ):
        pipeline_errors.update(_cache_pipeline_errors(records, dataset=dataset))
    result = build_s2_1_missingness_policy(
        versions["S2_REAL_VOLUME_RATIO"],
        daily_by_code=by_code(daily_records),
        basic_by_code=by_code(basic_records),
        flow_by_code=by_code(flow_records),
        histories=daily_history(),
        pipeline_errors=pipeline_errors,
        fail_on_pipeline_error=True,
    )
    config_hash = canonical_hash(config)
    engine_hash = file_sha256(
        ROOT / "quant" / "shadow" / "missingness_policy.py"
    )
    stock_by_code = by_code(_json(STOCK_BASIC_CACHE))
    s2_1_meta = _persist_s2_1(
        connection,
        result=result,
        s0_rows=versions["S0_LEGACY"],
        stock_by_code=stock_by_code,
        s2_run_id=old_meta["S2_REAL_VOLUME_RATIO"]["shadow_run_id"],
        config_hash=config_hash,
        engine_hash=engine_hash,
    )
    versions[VERSION_KEY] = result.ranked
    run_meta = {**old_meta, VERSION_KEY: s2_1_meta}

    threshold_rows = audit_threshold_dependencies(ROOT, versions)
    threshold_audit_id = _persist_threshold_audit(
        connection, threshold_rows, config_hash
    )
    coverage_by_code = {row["stock_code"]: row for row in result.audit_rows}
    registry_id, retrospective_samples = _persist_registry_and_retrospective(
        connection,
        config=config,
        config_hash=config_hash,
        versions=versions,
        run_meta=run_meta,
        coverage_by_code=coverage_by_code,
    )
    workbook_artifact_id = _persist_workbook_artifact(
        connection,
        prior=prior,
    )
    true_forward_samples = connection.execute(
        "SELECT COUNT(*) FROM quant_shadow_forward_sample "
        "WHERE registry_id=? AND sample_type='TRUE_FORWARD'",
        (registry_id,),
    ).fetchone()[0]
    full_detail_coverage = connection.execute(
        "SELECT COUNT(DISTINCT stock_code) FROM quant_shadow_factor_detail "
        "WHERE shadow_run_id=?",
        (s2_1_meta["shadow_run_id"],),
    ).fetchone()[0]
    detail_row_count = connection.execute(
        "SELECT COUNT(*) FROM quant_shadow_factor_detail WHERE shadow_run_id=?",
        (s2_1_meta["shadow_run_id"],),
    ).fetchone()[0]
    old_snapshot_after = _old_run_snapshot(connection, old_ids)
    if old_snapshot_before != old_snapshot_after:
        raise RuntimeError("HISTORICAL_S0_S4_MUTATION_DETECTED")
    connection.close()

    s2_summary = version_summary(
        "S2_REAL_VOLUME_RATIO",
        versions["S2_REAL_VOLUME_RATIO"],
        stock_by_code,
    )
    s2_1_summary = version_summary(VERSION_KEY, result.ranked, stock_by_code)
    s2_comparison = compare_versions(
        versions["S2_REAL_VOLUME_RATIO"],
        result.ranked,
        s2_summary,
        s2_1_summary,
    )
    s0_comparison = compare_versions(
        versions["S0_LEGACY"],
        result.ranked,
        version_summary("S0_LEGACY", versions["S0_LEGACY"], stock_by_code),
        s2_1_summary,
    )
    missing_codes = {
        row["stock_code"]
        for row in result.audit_rows
        if row["missing_class"] != "COMPLETE"
    }
    s2_top100 = {
        normalize_code(row["stock_code"])
        for row in versions["S2_REAL_VOLUME_RATIO"][:100]
    }
    s2_1_top100 = {
        normalize_code(row["stock_code"]) for row in result.ranked[:100]
    }
    missing_rate_before = len(missing_codes & s2_top100) / len(missing_codes)
    missing_rate_after = len(missing_codes & s2_1_top100) / len(missing_codes)
    qualified_dependencies = [
        row for row in threshold_rows if row["absolute_score_selected"]
    ]
    after = {
        "production_database_size": BASE_DATABASE.stat().st_size,
        "production_database_mtime": BASE_DATABASE.stat().st_mtime_ns,
        "historical_workbook_hash": file_sha256(BASE_WORKBOOK),
        "formal_hashes": source_hashes(),
    }
    if before != after:
        raise RuntimeError("PRODUCTION_IMMUTABILITY_VIOLATION")

    workbook_candidate = versioned_workbook_path(
        ROOT / "outputs" / TRADE_DATE,
        stem="智能交易助手",
        trade_date=TRADE_DATE,
        run_id=BASE_RUN_ID,
        factor_version="v0.3-phase4",
    )
    final_status = (
        "ABSOLUTE_SCORE_DEPENDENCY_FOUND"
        if qualified_dependencies
        else "FORWARD_SHADOW_READY"
    )
    report = {
        "phase": PHASE,
        "s2_1_factor_version": FACTOR_VERSION,
        "s2_1_shadow_run": s2_1_meta,
        "base_run": BASE_RUN_ID,
        "trade_date": TRADE_DATE,
        "input_hash": BASE_INPUT_HASH,
        "moneyflow_missing_count": result.summary["moneyflow_missing_count"],
        "normal_missing": result.summary["normal_missing_count"],
        "non_random_missing": result.summary["non_random_missing_count"],
        "data_insufficient": result.summary["data_insufficient_count"],
        "pipeline_errors": result.summary["pipeline_error_count"],
        "capital_reweighted_count": result.summary["capital_reweighted_count"],
        "capital_confidence_distribution": result.summary[
            "capital_confidence_distribution"
        ],
        "data_coverage_distribution": result.summary[
            "data_coverage_distribution"
        ],
        "missing_group_top100_rate_before": missing_rate_before,
        "missing_group_top100_rate_after": missing_rate_after,
        "s2_1_top20_overlap_vs_s2": s2_comparison["top20_overlap"],
        "s2_1_top50_overlap_vs_s2": s2_comparison["top50_overlap"],
        "s2_1_top100_overlap": s2_comparison["top100_overlap"],
        "s2_1_top100_flip_count": s2_comparison["top100_flip_count"],
        "s2_1_top100_overlap_vs_s0": s0_comparison["top100_overlap"],
        "absolute_score_threshold_dependencies": len(qualified_dependencies),
        "threshold_audit_id": threshold_audit_id,
        "threshold_qualification_flips": threshold_rows,
        "full_detail_coverage": full_detail_coverage,
        "full_detail_row_count": detail_row_count,
        "immutable_workbook_protection": {
            "status": "READY",
            "database_artifact_id": workbook_artifact_id,
            "database_binding": True,
            "versioned_candidate_path": str(workbook_candidate),
            "historical_workbook_unchanged": True,
            "binding_fields": [
                "run_id",
                "input_hash",
                "content_hash",
                "style_hash",
            ],
        },
        "forward_versions_frozen": list(FROZEN_VERSIONS),
        "forward_registry_id": registry_id,
        "forward_effective_trade_date": config["forward_effective_trade_date"],
        "retrospective_sample_type": "RETROSPECTIVE_DIAGNOSTIC",
        "retrospective_samples": retrospective_samples,
        "retrospective_version_cohorts": len(FROZEN_VERSIONS),
        "true_forward_samples": true_forward_samples,
        "forward_evaluation": {
            "execution_policy": "NEXT_OPEN",
            "horizons": ["T+1_OPEN", "D1", "D3", "D5"],
            "metrics": config["execution"]["metrics"],
            "outcomes_written": 0,
            "promotion_status": promotion_status(
                trading_days=0,
                complete_d3_tradable_samples=0,
                timing_contract_failures=0,
                data_coverage_explainable=True,
                stable_vs_s0=False,
            ),
        },
        "promotion_conclusion": PROMOTION_CONCLUSION,
        "production_changes": {
            "quant_algorithm": 0,
            "quant_weights": 0,
            "prompts": 0,
            "candidate_logic": 0,
            "production_config": 0,
            "engineering_only": [
                "future official workbook uses run_id/factor_version filename",
                "workbook manifest binds run_id/input_hash/content_hash/style_hash",
                "future official Quant persistence uses the full scored Universe",
                "frontend explains Legacy score is uncalibrated",
            ],
        },
        "runtime_audit": {
            "external_api_calls": 0,
            "llm_calls": 0,
            "orders": 0,
            "scheduler": "OFF",
            "production_database_unchanged": True,
            "historical_workbook_unchanged": True,
            "historical_s0_s4_unchanged": True,
            "quant_hash": after["formal_hashes"]["quant"],
            "flash_hash": prior["runtime_audit"]["flash_hash"],
            "pro_hash": prior["runtime_audit"]["pro_hash"],
            "git_commit": "NOT_EXECUTED",
        },
        "final_status": final_status,
    }
    output_root = (
        Path(args.output_dir).resolve()
        if args.output_dir
        else DEFAULT_OUTPUT_ROOT / s2_1_meta["shadow_run_id"]
    )
    output_root.mkdir(parents=True, exist_ok=True)
    paths = {
        "report_json": output_root / "quant_bugfix_candidate_report.json",
        "report_md": output_root / "quant_bugfix_candidate_report.md",
        "missingness": output_root / "s2_1_missingness_audit.csv",
        "score_changes": output_root / "s2_1_score_changes.csv",
        "membership": output_root / "s2_1_top_membership_changes.csv",
        "thresholds": output_root / "absolute_score_threshold_audit.csv",
        "registry": output_root / "forward_version_registry.csv",
        "readiness": output_root / "forward_evaluation_readiness.json",
        "workbook": output_root / "workbook_protection_audit.json",
    }
    _write_csv(paths["missingness"], result.audit_rows)
    _write_csv(
        paths["score_changes"],
        [
            {
                key: row[key]
                for key in (
                    "stock_code",
                    "s2_capital_score",
                    "s2_1_capital_score",
                    "capital_score_delta",
                    "s2_total_score",
                    "s2_1_total_score",
                    "quant_score_delta",
                    "s2_rank",
                    "s2_1_rank",
                    "rank_delta",
                    "capital_data_coverage",
                    "capital_confidence",
                )
            }
            for row in result.audit_rows
        ],
    )
    _write_csv(
        paths["membership"],
        _membership_changes(versions["S2_REAL_VOLUME_RATIO"], result.ranked),
    )
    _write_csv(paths["thresholds"], threshold_rows)
    _write_csv(
        paths["registry"],
        [
            {"version_key": key, **values}
            for key, values in config["versions"].items()
        ],
    )
    _write_json(paths["readiness"], report["forward_evaluation"])
    _write_json(paths["workbook"], report["immutable_workbook_protection"])
    report["reports"] = [str(path) for path in paths.values()]
    _write_json(paths["report_json"], report)
    paths["report_md"].write_text(_markdown(report), encoding="utf-8")
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Offline immutable S2.1 and forward freeze preparation"
    )
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--no-external-api", action="store_true", default=True)
    parser.add_argument("--no-llm", action="store_true", default=True)
    parser.add_argument("--no-orders", action="store_true", default=True)
    parser.add_argument("--shadow-only", action="store_true", default=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        report = run(parse_args(argv))
    except Exception as exc:
        print(
            json.dumps(
                {
                    "phase": PHASE,
                    "final_status": "SHADOW_BUILD_FAILED",
                    "error": f"{type(exc).__name__}:{exc}",
                },
                ensure_ascii=False,
            )
        )
        return 2
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "phase",
                    "s2_1_factor_version",
                    "moneyflow_missing_count",
                    "normal_missing",
                    "non_random_missing",
                    "pipeline_errors",
                    "capital_reweighted_count",
                    "s2_1_top100_overlap",
                    "absolute_score_threshold_dependencies",
                    "full_detail_coverage",
                    "retrospective_samples",
                    "true_forward_samples",
                    "production_changes",
                    "final_status",
                    "reports",
                )
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
